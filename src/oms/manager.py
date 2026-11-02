"""Order Management System (OMS) orchestrator linking intents, risk gating, execution, and fills."""

import asyncio
from datetime import datetime
from decimal import Decimal
import logging
from typing import Dict, List, Optional, Set
from uuid import uuid4

from src.broker.base import AbstractBrokerClient
from src.broker.models import BrokerOrderPayload, BrokerOrderResponse
from src.common.constants import ExecutionMode, OrderSide, OrderStatus, OrderType, ProductType
from src.common.exceptions import (
    BrokerConnectionError,
    InvalidStateTransitionError,
    RiskLimitBreachedError,
)
from src.common.time_utils import now_utc
from src.core.models import (
    BrokerOrder,
    Fill,
    OptionContract,
    OrderRequest,
    Position,
    TradingSession,
)
from src.oms.idempotency import IdempotencyTracker
from src.oms.lifecycle import can_transition_oms_order, validate_oms_transition
from src.oms.models import (
    OmsOrder,
    OmsOrderStateTransition,
    OmsOrderStatus,
    OrderIntent,
    map_fyers_status_to_oms,
    map_oms_status_to_fyers,
)
from src.risk.engine import RiskEngine
from src.storage.repository import StateRepository

logger = logging.getLogger(__name__)


class OrderManagementSystem:
    """
    Central Order Management System (OMS) ensuring:
    - Guaranteed pre-trade risk validation through the PreTradeGatekeeper.
    - Deterministic client-side idempotency and duplicate-submission prevention.
    - Zero blind retries on ambiguous network timeouts.
    - Authoritative fill accounting, deduplication, and partial fill tracking.
    - Two-phase atomic reversal sequencing.
    - Auditable persistence via StateRepository.
    """

    def __init__(
        self,
        broker_client: AbstractBrokerClient,
        risk_engine: RiskEngine,
        session: TradingSession,
        state_repo: Optional[StateRepository] = None,
        idempotency_tracker: Optional[IdempotencyTracker] = None,
    ) -> None:
        self.broker_client = broker_client
        self.risk_engine = risk_engine
        self.session = session
        self.state_repo = state_repo
        self.idempotency = idempotency_tracker or IdempotencyTracker()

        # In-memory OMS tracking maps
        self._orders: Dict[str, OmsOrder] = {}
        self._broker_to_client: Dict[str, str] = {}
        self._processed_fill_ids: Set[str] = set()
        self._positions: Dict[str, Position] = {}

    def get_order(self, client_order_id: str) -> Optional[OmsOrder]:
        """Fetch active or historical order by client_order_id."""
        return self._orders.get(client_order_id)

    def get_order_by_broker_id(self, broker_order_id: str) -> Optional[OmsOrder]:
        """Fetch order by broker-assigned order ID."""
        client_id = self._broker_to_client.get(broker_order_id)
        if client_id:
            return self._orders.get(client_id)
        return None

    def get_all_orders(self) -> List[OmsOrder]:
        """Fetch all orders in current session."""
        return list(self._orders.values())

    def get_open_orders(self) -> List[OmsOrder]:
        """Fetch working/unfilled orders."""
        open_states = {
            OmsOrderStatus.INTENT_CREATED,
            OmsOrderStatus.VALIDATING,
            OmsOrderStatus.SUBMISSION_PENDING,
            OmsOrderStatus.ACKNOWLEDGED,
            OmsOrderStatus.OPEN,
            OmsOrderStatus.PARTIALLY_FILLED,
            OmsOrderStatus.CANCELLATION_REQUESTED,
            OmsOrderStatus.RECONCILIATION_REQUIRED,
        }
        return [o for o in self._orders.values() if o.oms_status in open_states]

    def get_position(self, symbol: str) -> Optional[Position]:
        """Fetch current internal position for a symbol."""
        return self._positions.get(symbol)

    def get_all_positions(self) -> List[Position]:
        """Fetch all internal positions."""
        return list(self._positions.values())

    def is_position_flat(self, symbol: str) -> bool:
        """True if no open position exists for the symbol (net_qty == 0)."""
        pos = self._positions.get(symbol)
        if pos is None:
            return True
        return pos.net_qty == 0

    # --------------------------------------------------------------------------
    # 1. Order Creation & Submission Workflow
    # --------------------------------------------------------------------------

    async def submit_order_intent(
        self,
        intent: OrderIntent,
        contract: Optional[OptionContract] = None,
        evaluation_time: Optional[datetime] = None,
    ) -> OmsOrder:
        """
        Process strategy order intent through pre-trade gating, persistence,
        and broker submission.
        """
        # Step 1: Idempotency check
        if self.idempotency.is_duplicate(intent.intent_id, intent.client_order_id):
            logger.warning(
                f"[OMS Idempotency] Duplicate order intent rejected: {intent.intent_id} "
                f"({intent.client_order_id})"
            )
            existing = self._orders.get(intent.client_order_id)
            if existing:
                return existing
            # If not yet registered locally, create rejected order
            order = self._create_initial_order(intent, OmsOrderStatus.REJECTED, "Duplicate intent rejected")
            return order

        # Register intent
        self.idempotency.register(intent.intent_id, intent.client_order_id)

        # Step 2: Initialize OmsOrder in INTENT_CREATED
        order = self._create_initial_order(intent, OmsOrderStatus.INTENT_CREATED, "Intent created")

        # Step 3: Pre-trade risk validation
        self._transition_order(order, OmsOrderStatus.VALIDATING, "Validating pre-trade risk controls")

        order_req = OrderRequest(
            client_order_id=intent.client_order_id,
            session_id=intent.session_id,
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            qty=intent.qty,
            product_type=intent.product_type,
            limit_price=intent.limit_price,
            stop_price=intent.stop_price,
            tag=intent.tag,
        )

        validation = self.risk_engine.validate_order(
            order_request=order_req,
            execution_mode=self.session.execution_mode,
            session=self.session,
            contract=contract,
            current_position=self._positions.get(intent.symbol),
            pending_orders=[self.to_broker_order(o) for o in self.get_open_orders() if o.client_order_id != intent.client_order_id],
            evaluation_time=evaluation_time or intent.created_at,
        )

        if not validation.is_valid:
            logger.warning(
                f"[OMS Pre-Trade REJECT] Order {intent.client_order_id} rejected by risk gatekeeper: {validation.message}"
            )
            self._transition_order(order, OmsOrderStatus.REJECTED, f"Risk rejected: {validation.message}")
            await self._persist_order(order)
            return order

        # Step 4: Approved -> Transition to SUBMISSION_PENDING & Persist
        self._transition_order(order, OmsOrderStatus.SUBMISSION_PENDING, "Pre-trade risk approved")
        await self._persist_order(order)

        # Step 5: Broker submission
        payload = BrokerOrderPayload(
            symbol=intent.symbol,
            qty=intent.qty,
            type=intent.order_type,
            side=intent.side,
            product_type=intent.product_type,
            limit_price=intent.limit_price,
            stop_price=intent.stop_price,
            validity="DAY",
            tag=intent.tag,
            client_order_id=intent.client_order_id,
        )

        try:
            response = await self.broker_client.place_order(payload)
        except Exception as e:
            logger.critical(f"[OMS Broker Failure] Unhandled error submitting {intent.client_order_id}: {e}")
            self._transition_order(
                order,
                OmsOrderStatus.RECONCILIATION_REQUIRED,
                f"Submission error: {e}. Reconciliation required.",
            )
            await self._persist_order(order)
            return order

        # Step 6: Process broker response
        if response.is_timeout_ambiguous:
            logger.critical(
                f"[OMS TIMEOUT] Order {intent.client_order_id} submission timed out. "
                "Outcome is ambiguous. Marked RECONCILIATION_REQUIRED. Zero blind retries permitted."
            )
            self._transition_order(
                order,
                OmsOrderStatus.RECONCILIATION_REQUIRED,
                "Broker submission timed out. Marked for reconciliation.",
            )
            await self._persist_order(order)
            return order

        if response.success:
            order.broker_order_id = response.broker_order_id
            if response.broker_order_id:
                self._broker_to_client[response.broker_order_id] = intent.client_order_id

            self._transition_order(
                order,
                OmsOrderStatus.ACKNOWLEDGED,
                f"Acknowledged by broker (ID: {response.broker_order_id})",
            )
            # Transition to OPEN in orderbook
            self._transition_order(order, OmsOrderStatus.OPEN, "Open in broker orderbook")

            # In simulation / paper mode, if broker immediately executed trades:
            if hasattr(self.broker_client, "get_trades_for_order") and response.broker_order_id:
                trades = self.broker_client.get_trades_for_order(response.broker_order_id)
                for trd in trades:
                    fill = Fill(
                        fill_id=trd.trade_id,
                        client_order_id=order.client_order_id,
                        broker_order_id=order.broker_order_id,
                        symbol=order.symbol,
                        side=order.side,
                        fill_price=trd.traded_price,
                        fill_qty=trd.traded_qty,
                        timestamp=trd.trade_time,
                    )
                    await self.on_fill_event(fill)
        else:
            self._transition_order(
                order,
                OmsOrderStatus.REJECTED,
                f"Broker rejected: {response.message} (Code {response.status_code})",
            )

        await self._persist_order(order)
        return order

    # --------------------------------------------------------------------------
    # 2. Execution Fills & Position Lifecycle
    # --------------------------------------------------------------------------

    async def on_fill_event(self, fill: Fill) -> Optional[OmsOrder]:
        """
        Process broker execution fill report with strict deduplication
        and cumulative volume accounting.
        """
        # Deduplicate fill events
        if fill.fill_id in self._processed_fill_ids:
            logger.warning(f"[OMS Fill] Duplicate fill event ignored: {fill.fill_id}")
            return self.get_order(fill.client_order_id)

        self._processed_fill_ids.add(fill.fill_id)

        order = self.get_order(fill.client_order_id)
        if order is None and fill.broker_order_id:
            order = self.get_order_by_broker_id(fill.broker_order_id)

        if order is None:
            logger.critical(
                f"[OMS Fill ERROR] Received fill for unknown order: Client={fill.client_order_id}, "
                f"Broker={fill.broker_order_id}, Symbol={fill.symbol}, Qty={fill.fill_qty}"
            )
            return None

        # Update order fill statistics
        order.fills.append(fill)
        order.filled_qty += fill.fill_qty
        order.remaining_qty = max(0, order.qty - order.filled_qty)

        # Calculate exact weighted average fill price
        total_val = sum((f.fill_price * Decimal(f.fill_qty) for f in order.fills), Decimal("0.00"))
        if order.filled_qty > 0:
            order.avg_fill_price = (total_val / Decimal(order.filled_qty)).quantize(Decimal("0.01"))

        # Transition status based on filled volume
        if order.filled_qty >= order.qty:
            self._transition_order(
                order,
                OmsOrderStatus.FILLED,
                f"Filled completely ({order.filled_qty}/{order.qty}) at avg {order.avg_fill_price}",
            )
        else:
            self._transition_order(
                order,
                OmsOrderStatus.PARTIALLY_FILLED,
                f"Partially filled ({order.filled_qty}/{order.qty}) at {fill.fill_price}",
            )

        # Update internal position
        pos = self._positions.get(order.symbol)
        if pos is None:
            pos = Position(
                session_id=order.session_id,
                symbol=order.symbol,
                product_type=order.product_type,
            )
            self._positions[order.symbol] = pos

        pos.apply_fill(fill)

        # Update Risk Engine accounting
        self.risk_engine.on_fill(fill)

        # Persist to database if repository configured
        if self.state_repo:
            await self.state_repo.record_fill(fill)
            await self._persist_order(order)
            await self.state_repo.save_position(pos)

        return order

    # --------------------------------------------------------------------------
    # 3. Order Cancellation
    # --------------------------------------------------------------------------

    async def request_cancel_order(self, client_order_id: str, reason: str = "") -> BrokerOrderResponse:
        """
        Request cancellation of an active order.
        Marks CANCELLATION_REQUESTED; does NOT mark CANCELLED until broker confirms.
        """
        order = self.get_order(client_order_id)
        if not order:
            return BrokerOrderResponse(
                success=False,
                client_order_id=client_order_id,
                status_code=404,
                message=f"Order {client_order_id} not found in OMS",
            )

        if order.oms_status in {OmsOrderStatus.FILLED, OmsOrderStatus.CANCELLED, OmsOrderStatus.REJECTED, OmsOrderStatus.FAILED}:
            return BrokerOrderResponse(
                success=False,
                broker_order_id=order.broker_order_id,
                client_order_id=client_order_id,
                status_code=400,
                message=f"Order is already terminal ({order.oms_status.value})",
            )

        self._transition_order(order, OmsOrderStatus.CANCELLATION_REQUESTED, reason or "Cancel requested")
        await self._persist_order(order)

        if not order.broker_order_id:
            # If never reached broker, cancel locally
            self._transition_order(order, OmsOrderStatus.CANCELLED, "Cancelled before broker submission")
            await self._persist_order(order)
            return BrokerOrderResponse(
                success=True,
                client_order_id=client_order_id,
                status_code=200,
                message="Cancelled locally",
            )

        resp = await self.broker_client.cancel_order(order.broker_order_id)
        if resp.success:
            self._transition_order(order, OmsOrderStatus.CANCELLED, "Cancellation confirmed by broker")
            await self._persist_order(order)
        return resp

    # --------------------------------------------------------------------------
    # 4. Out-of-Order Broker Updates & State Transitions
    # --------------------------------------------------------------------------

    def on_broker_order_update(
        self,
        client_order_id: str,
        broker_order_id: Optional[str] = None,
        broker_status: Optional[OrderStatus] = None,
        message: str = "",
    ) -> Optional[OmsOrder]:
        """
        Ingest an asynchronous broker order status update (e.g. from WebSocket).
        Maps broker status to OmsOrderStatus and applies transition rules.
        Rejects out-of-order regression of terminal states.
        """
        order = self.get_order(client_order_id)
        if order is None and broker_order_id:
            order = self.get_order_by_broker_id(broker_order_id)

        if order is None:
            logger.warning(
                f"[OMS Broker Update] Received update for unknown order: Client={client_order_id}, "
                f"Broker={broker_order_id}"
            )
            return None

        if broker_order_id and not order.broker_order_id:
            order.broker_order_id = broker_order_id
            self._broker_to_client[broker_order_id] = order.client_order_id

        if broker_status is None:
            return order

        target_status = map_fyers_status_to_oms(broker_status, order.filled_qty, order.qty)

        # If order is already in a terminal state, ignore out-of-order updates that would regress
        if order.oms_status in {OmsOrderStatus.FILLED, OmsOrderStatus.CANCELLED, OmsOrderStatus.REJECTED, OmsOrderStatus.FAILED}:
            if order.oms_status != target_status:
                logger.warning(
                    f"[OMS Lifecycle] Ignoring out-of-order broker update for terminal order "
                    f"{order.client_order_id}: current={order.oms_status.value}, received={target_status.value}"
                )
            return order

        if can_transition_oms_order(order.oms_status, target_status):
            self._transition_order(order, target_status, message or f"Broker update {broker_status.name}")
        else:
            logger.warning(
                f"[OMS Lifecycle] Illegal broker update transition: {order.oms_status.value} -> {target_status.value}"
            )

        return order

    # --------------------------------------------------------------------------
    # 5. Two-Phase Reversal Coordination
    # --------------------------------------------------------------------------

    async def coordinate_two_phase_reversal(
        self,
        exit_intent: OrderIntent,
        entry_intent: OrderIntent,
        exit_contract: Optional[OptionContract] = None,
        entry_contract: Optional[OptionContract] = None,
        evaluation_time: Optional[datetime] = None,
    ) -> tuple[OmsOrder, Optional[OmsOrder]]:
        """
        Execute an atomic two-phase reversal:
        Phase 1: Submit exit order to close existing position.
        Phase 2: SUBMIT REPLACEMENT ONLY AFTER Phase 1 reaches terminal status FILLED.
        If Phase 1 fails, is rejected, or remains unfilled, Phase 2 is aborted.
        """
        logger.info(f"[OMS Reversal] Phase 1: Submitting exit intent {exit_intent.client_order_id}")
        exit_order = await self.submit_order_intent(exit_intent, contract=exit_contract, evaluation_time=evaluation_time)

        if exit_order.oms_status != OmsOrderStatus.FILLED:
            logger.warning(
                f"[OMS Reversal] Phase 1 not filled (status: {exit_order.oms_status.value}). "
                f"Aborting Phase 2 replacement order {entry_intent.client_order_id}."
            )
            return exit_order, None

        logger.info(
            f"[OMS Reversal] Phase 1 FILLED successfully. Proceeding to Phase 2: {entry_intent.client_order_id}"
        )
        entry_order = await self.submit_order_intent(entry_intent, contract=entry_contract, evaluation_time=evaluation_time)
        return exit_order, entry_order

    def _transition_order(
        self,
        order: OmsOrder,
        target_status: OmsOrderStatus,
        message: str = "",
    ) -> None:
        """
        Enforce valid state transition graph.
        Rejects regression of terminal states caused by out-of-order broker messages.
        """
        current_status = order.oms_status
        if current_status == target_status:
            return

        if not can_transition_oms_order(current_status, target_status):
            logger.warning(
                f"[OMS Lifecycle] Illegal transition rejected for {order.client_order_id}: "
                f"{current_status.value} -> {target_status.value}"
            )
            raise InvalidStateTransitionError(
                entity=f"OmsOrder[{order.client_order_id}]",
                current_state=current_status.value,
                attempted_state=target_status.value,
            )

        order.oms_status = target_status
        order.status_message = message
        order.updated_at = now_utc()

        transition = OmsOrderStateTransition(
            client_order_id=order.client_order_id,
            from_status=current_status,
            to_status=target_status,
            filled_qty=order.filled_qty,
            avg_price=order.avg_fill_price,
            message=message,
            timestamp=order.updated_at,
        )
        order.transitions.append(transition)

    def _create_initial_order(
        self,
        intent: OrderIntent,
        initial_status: OmsOrderStatus,
        message: str,
    ) -> OmsOrder:
        """Instantiate and record initial OmsOrder."""
        order = OmsOrder(
            client_order_id=intent.client_order_id,
            session_id=intent.session_id,
            intent_id=intent.intent_id,
            strategy_name=intent.strategy_name,
            strategy_version=intent.strategy_version,
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            product_type=intent.product_type,
            qty=intent.qty,
            filled_qty=0,
            remaining_qty=intent.qty,
            oms_status=initial_status,
            avg_fill_price=Decimal("0.00"),
            limit_price=intent.limit_price,
            tag=intent.tag,
            status_message=message,
        )
        order.transitions.append(
            OmsOrderStateTransition(
                client_order_id=order.client_order_id,
                from_status=OmsOrderStatus.INTENT_CREATED,
                to_status=initial_status,
                filled_qty=0,
                avg_price=Decimal("0.00"),
                message=message,
                timestamp=order.created_at,
            )
        )
        self._orders[intent.client_order_id] = order
        return order

    async def _persist_order(self, order: OmsOrder) -> None:
        """Persist order and state transitions via StateRepository."""
        if not self.state_repo:
            return

        broker_ord = self.to_broker_order(order)
        try:
            existing = await self.state_repo.get_order(order.client_order_id)
            if existing is None:
                await self.state_repo.save_order(broker_ord)
            else:
                last_trans = order.transitions[-1] if order.transitions else None
                from_st = map_oms_status_to_fyers(last_trans.from_status) if last_trans else OrderStatus.NEW
                await self.state_repo.update_order_status(
                    client_order_id=order.client_order_id,
                    from_status=from_st,
                    to_status=map_oms_status_to_fyers(order.oms_status),
                    filled_qty=order.filled_qty,
                    remaining_qty=order.remaining_qty,
                    avg_fill_price=order.avg_fill_price,
                    broker_order_id=order.broker_order_id,
                    status_message=order.status_message,
                    updated_at=order.updated_at,
                )
        except Exception as e:
            logger.error(f"[OMS Persistence Error] Failed to persist order {order.client_order_id}: {e}")

    @staticmethod
    def to_broker_order(oms_order: OmsOrder) -> BrokerOrder:
        """Convert OmsOrder into core BrokerOrder model."""
        return BrokerOrder(
            client_order_id=oms_order.client_order_id,
            broker_order_id=oms_order.broker_order_id,
            session_id=oms_order.session_id,
            symbol=oms_order.symbol,
            side=oms_order.side,
            order_type=oms_order.order_type,
            product_type=oms_order.product_type,
            qty=oms_order.qty,
            filled_qty=oms_order.filled_qty,
            remaining_qty=oms_order.remaining_qty,
            status=map_oms_status_to_fyers(oms_order.oms_status),
            avg_fill_price=oms_order.avg_fill_price,
            limit_price=oms_order.limit_price,
            tag=oms_order.tag,
            status_message=oms_order.status_message,
            submitted_at=oms_order.created_at,
            updated_at=oms_order.updated_at,
        )
