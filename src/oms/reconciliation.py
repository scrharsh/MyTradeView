"""Broker state reconciliation service comparing internal OMS records against broker books."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from src.broker.base import AbstractBrokerClient
from src.broker.models import BrokerConnectionStatus, BrokerOrderBookItem, BrokerPosition
from src.common.time_utils import now_utc
from src.oms.manager import OrderManagementSystem
from src.oms.models import OmsOrder, OmsOrderStatus, map_fyers_status_to_oms

logger = logging.getLogger(__name__)


class DiscrepancyType(str, Enum):
    """Categorization of discrepancies between internal OMS and broker state."""
    MISSING_ON_BROKER = "MISSING_ON_BROKER"
    UNKNOWN_BROKER_ORDER = "UNKNOWN_BROKER_ORDER"
    STATUS_DESYNC = "STATUS_DESYNC"
    QTY_MISMATCH = "QTY_MISMATCH"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    POSITION_DESYNC = "POSITION_DESYNC"
    RESIDUAL_POSITION = "RESIDUAL_POSITION"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"


class ReconciliationDiscrepancy(BaseModel):
    """Structured record of an identified mismatch between OMS and broker."""
    model_config = ConfigDict(frozen=True)

    discrepancy_type: DiscrepancyType
    identifier: str
    symbol: str
    internal_value: Any
    broker_value: Any
    severity: str = "HIGH"  # INFO, WARNING, HIGH, CRITICAL
    details: str = ""


class ReconciliationReport(BaseModel):
    """Comprehensive snapshot of a broker reconciliation execution."""
    model_config = ConfigDict(frozen=True)

    is_synchronized: bool
    is_broker_available: bool
    timestamp: datetime = Field(default_factory=now_utc)
    internal_orders_count: int
    broker_orders_count: int
    internal_positions_count: int
    broker_positions_count: int
    discrepancies: List[ReconciliationDiscrepancy] = Field(default_factory=list)
    summary: str = ""


class BrokerReconciliationService:
    """
    Independent reconciliation service comparing internal orders, fills,
    and positions with authoritative broker books.
    
    CRITICAL SAFETY RULES:
    - Never automatically liquidates positions or cancels unknown orders during reconciliation.
    - If the broker is unreachable, reports reconciliation as unavailable rather than assuming internal state is correct.
    """

    def __init__(self, oms: OrderManagementSystem, broker_client: AbstractBrokerClient) -> None:
        self.oms = oms
        self.broker = broker_client

    async def reconcile(self) -> ReconciliationReport:
        """
        Execute comprehensive reconciliation against broker order book and positions.
        """
        # Step 0: Check broker connectivity
        if self.broker.connection_status in {BrokerConnectionStatus.DISCONNECTED, BrokerConnectionStatus.ERROR}:
            logger.warning("[Reconciliation] Broker is disconnected or unreachable. Reconciliation unavailable.")
            return ReconciliationReport(
                is_synchronized=False,
                is_broker_available=False,
                internal_orders_count=len(self.oms.get_all_orders()),
                broker_orders_count=0,
                internal_positions_count=len(self.oms.get_all_positions()),
                broker_positions_count=0,
                discrepancies=[
                    ReconciliationDiscrepancy(
                        discrepancy_type=DiscrepancyType.BROKER_UNAVAILABLE,
                        identifier="BROKER_CONNECTION",
                        symbol="N/A",
                        internal_value="ACTIVE",
                        broker_value=self.broker.connection_status.value,
                        severity="CRITICAL",
                        details="Broker API unreachable. Cannot verify authoritative broker state.",
                    )
                ],
                summary="Reconciliation unavailable: Broker API disconnected or in error state.",
            )

        try:
            broker_orders = await self.broker.get_order_book()
            broker_positions = await self.broker.get_positions()
        except Exception as e:
            logger.error(f"[Reconciliation Error] Broker communication failed during reconciliation: {e}")
            return ReconciliationReport(
                is_synchronized=False,
                is_broker_available=False,
                internal_orders_count=len(self.oms.get_all_orders()),
                broker_orders_count=0,
                internal_positions_count=len(self.oms.get_all_positions()),
                broker_positions_count=0,
                discrepancies=[
                    ReconciliationDiscrepancy(
                        discrepancy_type=DiscrepancyType.BROKER_UNAVAILABLE,
                        identifier="QUERY_EXCEPTION",
                        symbol="N/A",
                        internal_value="OK",
                        broker_value=str(e),
                        severity="CRITICAL",
                        details=f"Exception querying broker books: {e}",
                    )
                ],
                summary=f"Reconciliation query failed: {e}",
            )

        discrepancies: List[ReconciliationDiscrepancy] = []

        # ----------------------------------------------------------------------
        # A. Order Book Reconciliation
        # ----------------------------------------------------------------------
        internal_orders = self.oms.get_all_orders()
        broker_order_map: Dict[str, BrokerOrderBookItem] = {b.broker_order_id: b for b in broker_orders}
        client_to_broker_map: Dict[str, BrokerOrderBookItem] = {
            b.client_order_id: b for b in broker_orders if b.client_order_id
        }

        # 1. Check internal orders against broker
        for int_ord in internal_orders:
            # Ignore orders that failed locally before submission attempt
            if int_ord.oms_status in {OmsOrderStatus.INTENT_CREATED, OmsOrderStatus.VALIDATING, OmsOrderStatus.REJECTED}:
                continue

            brk_match: Optional[BrokerOrderBookItem] = None
            if int_ord.broker_order_id and int_ord.broker_order_id in broker_order_map:
                brk_match = broker_order_map[int_ord.broker_order_id]
            elif int_ord.client_order_id in client_to_broker_map:
                brk_match = client_to_broker_map[int_ord.client_order_id]

            if brk_match is None:
                # Order submitted or open according to OMS, but missing from broker order book
                if int_ord.oms_status not in {OmsOrderStatus.FAILED, OmsOrderStatus.CANCELLED}:
                    discrepancies.append(
                        ReconciliationDiscrepancy(
                            discrepancy_type=DiscrepancyType.MISSING_ON_BROKER,
                            identifier=int_ord.client_order_id,
                            symbol=int_ord.symbol,
                            internal_value=int_ord.oms_status.value,
                            broker_value="MISSING",
                            severity="HIGH",
                            details=(
                                f"Order {int_ord.client_order_id} is in internal state "
                                f"{int_ord.oms_status.value} but absent from broker order book."
                            ),
                        )
                    )
            else:
                # Compare Status
                expected_oms_st = map_fyers_status_to_oms(brk_match.status, brk_match.filled_qty, brk_match.qty)
                if int_ord.oms_status != expected_oms_st:
                    # Filter out transient validation states
                    if not (int_ord.oms_status == OmsOrderStatus.CANCELLATION_REQUESTED and expected_oms_st == OmsOrderStatus.OPEN):
                        discrepancies.append(
                            ReconciliationDiscrepancy(
                                discrepancy_type=DiscrepancyType.STATUS_DESYNC,
                                identifier=int_ord.client_order_id,
                                symbol=int_ord.symbol,
                                internal_value=int_ord.oms_status.value,
                                broker_value=expected_oms_st.value,
                                severity="WARNING" if expected_oms_st in {OmsOrderStatus.PARTIALLY_FILLED, OmsOrderStatus.FILLED} else "HIGH",
                                details=(
                                    f"Status desync: Internal={int_ord.oms_status.value}, "
                                    f"Broker={expected_oms_st.value} (Code {brk_match.status.value})"
                                ),
                            )
                        )

                # Compare Filled Qty
                if int_ord.filled_qty != brk_match.filled_qty:
                    discrepancies.append(
                        ReconciliationDiscrepancy(
                            discrepancy_type=DiscrepancyType.QTY_MISMATCH,
                            identifier=int_ord.client_order_id,
                            symbol=int_ord.symbol,
                            internal_value=int_ord.filled_qty,
                            broker_value=brk_match.filled_qty,
                            severity="HIGH",
                            details=f"Filled quantity mismatch: Internal={int_ord.filled_qty}, Broker={brk_match.filled_qty}",
                        )
                    )

                # Compare Avg Price on fills
                if brk_match.avg_price > 0 and int_ord.avg_fill_price > 0:
                    diff = abs(int_ord.avg_fill_price - brk_match.avg_price)
                    if diff > Decimal("0.05"):
                        discrepancies.append(
                            ReconciliationDiscrepancy(
                                discrepancy_type=DiscrepancyType.PRICE_MISMATCH,
                                identifier=int_ord.client_order_id,
                                symbol=int_ord.symbol,
                                internal_value=str(int_ord.avg_fill_price),
                                broker_value=str(brk_match.avg_price),
                                severity="WARNING",
                                details=f"Average price mismatch: Internal={int_ord.avg_fill_price}, Broker={brk_match.avg_price}",
                            )
                        )

        # 2. Check for unknown orders on broker
        for brk_ord in broker_orders:
            int_match = None
            if brk_ord.client_order_id:
                int_match = self.oms.get_order(brk_ord.client_order_id)
            if int_match is None:
                int_match = self.oms.get_order_by_broker_id(brk_ord.broker_order_id)

            if int_match is None:
                discrepancies.append(
                    ReconciliationDiscrepancy(
                        discrepancy_type=DiscrepancyType.UNKNOWN_BROKER_ORDER,
                        identifier=brk_ord.broker_order_id,
                        symbol=brk_ord.symbol,
                        internal_value="UNTRACKED",
                        broker_value=brk_ord.status.name,
                        severity="CRITICAL",
                        details=(
                            f"Unknown broker order {brk_ord.broker_order_id} ({brk_ord.symbol} "
                            f"{brk_ord.side.name} {brk_ord.qty} Qty) not found in local OMS."
                        ),
                    )
                )

        # ----------------------------------------------------------------------
        # B. Positions Reconciliation
        # ----------------------------------------------------------------------
        internal_positions = {p.symbol: p for p in self.oms.get_all_positions()}
        broker_position_map = {p.symbol: p for p in broker_positions}

        all_symbols = set(internal_positions.keys()).union(set(broker_position_map.keys()))
        for sym in all_symbols:
            int_pos = internal_positions.get(sym)
            brk_pos = broker_position_map.get(sym)

            int_net = int_pos.net_qty if int_pos else 0
            brk_net = brk_pos.net_qty if brk_pos else 0

            if int_net != brk_net:
                discrepancies.append(
                    ReconciliationDiscrepancy(
                        discrepancy_type=DiscrepancyType.POSITION_DESYNC,
                        identifier=sym,
                        symbol=sym,
                        internal_value=int_net,
                        broker_value=brk_net,
                        severity="CRITICAL",
                        details=f"Position net quantity desync for {sym}: Internal={int_net}, Broker={brk_net}",
                    )
                )

        is_sync = len(discrepancies) == 0
        summary = (
            "System state fully synchronized with broker."
            if is_sync
            else f"Reconciliation detected {len(discrepancies)} discrepancies."
        )

        logger.info(f"[Reconciliation Complete] Synchronized: {is_sync}. {summary}")
        return ReconciliationReport(
            is_synchronized=is_sync,
            is_broker_available=True,
            internal_orders_count=len(internal_orders),
            broker_orders_count=len(broker_orders),
            internal_positions_count=len(internal_positions),
            broker_positions_count=len(broker_positions),
            discrepancies=discrepancies,
            summary=summary,
        )
