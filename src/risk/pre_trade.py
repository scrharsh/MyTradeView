"""Centralized Pre-Trade Risk Gatekeeper.

Performs deterministic multi-point validation on all proposed orders before
they can reach the order management system (OMS) or broker execution adapter.
"""

from datetime import datetime, time
from decimal import Decimal
import logging
from typing import Dict, List, Optional, Set

from src.common.constants import (
    ExecutionMode,
    OrderSide,
    OrderStatus,
    OrderType,
    RiskRejectReason,
)
from src.common.time_utils import is_trading_day, is_within_time_window, now_utc, to_ist
from src.core.models import BrokerOrder, OptionContract, OrderRequest, Position, TradingSession
from src.risk.base import PreTradeValidationResult

logger = logging.getLogger(__name__)


class PreTradeGatekeeper:
    """
    Tier-1 Gatekeeper enforcing hard pre-trade risk controls and regulatory limits.
    
    Guarantees that no unauthorized, duplicate, stale, out-of-session, or mis-sized
    orders can reach broker execution adapters.
    """

    def __init__(
        self,
        allowed_trading_days: Optional[List[str]] = None,
        orb_start_time: time = time(9, 16, 0),
        mandatory_exit_time: time = time(15, 10, 0),
        staleness_threshold_seconds: int = 60,
    ) -> None:
        self.allowed_trading_days = allowed_trading_days or ["Monday", "Thursday", "Friday"]
        self.orb_start_time = orb_start_time
        self.mandatory_exit_time = mandatory_exit_time
        self.staleness_threshold_seconds = staleness_threshold_seconds

        self._submitted_client_order_ids: Set[str] = set()

    def register_client_order_id(self, client_order_id: str) -> None:
        """Record client order ID for idempotency tracking."""
        self._submitted_client_order_ids.add(client_order_id)

    def validate_order_request(
        self,
        order_request: OrderRequest,
        execution_mode: ExecutionMode,
        session: TradingSession,
        contract: Optional[OptionContract],
        is_kill_switch_active: bool = False,
        is_profit_protection_locked: bool = False,
        current_position: Optional[Position] = None,
        pending_orders: Optional[List[BrokerOrder]] = None,
        last_market_data_time: Optional[datetime] = None,
        evaluation_time: Optional[datetime] = None,
    ) -> PreTradeValidationResult:
        """
        Validate proposed order request across all pre-trade compliance checks.
        
        Returns:
            PreTradeValidationResult with is_valid=True on success, or
            is_valid=False with specific RiskRejectReason and audit context on failure.
        """
        eval_dt = evaluation_time or now_utc()
        eval_ist = to_ist(eval_dt)
        current_time_ist = eval_ist.time()

        is_entry = order_request.side == OrderSide.BUY

        # ----------------------------------------------------------------------
        # 1. Execution Mode Safety Interlock
        # ----------------------------------------------------------------------
        if execution_mode == ExecutionMode.LIVE:
            logger.critical("[PreTrade GATE] Live execution order intercepted in paper/simulation environment!")
            return PreTradeValidationResult(
                is_valid=False,
                reason_code=RiskRejectReason.EXECUTION_MODE_DISABLED,
                message="Live trading execution is disabled by fail-closed safety interlock.",
                context={"client_order_id": order_request.client_order_id, "mode": execution_mode.value},
            )

        # ----------------------------------------------------------------------
        # 2. Idempotency & Duplicate Order Check
        # ----------------------------------------------------------------------
        if order_request.client_order_id in self._submitted_client_order_ids:
            return PreTradeValidationResult(
                is_valid=False,
                reason_code=RiskRejectReason.DUPLICATE_ORDER,
                message=f"Duplicate order submission rejected: ClientOrderId '{order_request.client_order_id}' already processed.",
                context={"client_order_id": order_request.client_order_id},
            )

        # ----------------------------------------------------------------------
        # 3. Kill-Switch & Loss Lockout Check (Entry Orders Only)
        # ----------------------------------------------------------------------
        if is_entry and is_kill_switch_active:
            return PreTradeValidationResult(
                is_valid=False,
                reason_code=RiskRejectReason.KILL_SWITCH_ACTIVE,
                message="New entry order rejected: Daily loss limit kill-switch is active.",
                context={"client_order_id": order_request.client_order_id},
            )

        # ----------------------------------------------------------------------
        # 4. Profit Protection Lockout Check (Entry Orders Only)
        # ----------------------------------------------------------------------
        if is_entry and is_profit_protection_locked:
            return PreTradeValidationResult(
                is_valid=False,
                reason_code=RiskRejectReason.PROFIT_PROTECTION_LOCKED,
                message="New entry order rejected: Strategy profit protection floor is locked for session.",
                context={"client_order_id": order_request.client_order_id},
            )

        # ----------------------------------------------------------------------
        # 5. Session Phase & Trading Hours Check (Entry Orders Only)
        # ----------------------------------------------------------------------
        if is_entry:
            if not session.is_active:
                return PreTradeValidationResult(
                    is_valid=False,
                    reason_code=RiskRejectReason.OUTSIDE_TRADING_HOURS,
                    message=f"Order rejected: Trading session '{session.session_id}' is inactive.",
                    context={"session_id": session.session_id},
                )

            # Day of week filter
            if not is_trading_day(eval_dt, self.allowed_trading_days):
                return PreTradeValidationResult(
                    is_valid=False,
                    reason_code=RiskRejectReason.NON_TRADING_DAY,
                    message=f"Order rejected: {eval_ist.strftime('%A')} is not an allowed trading day {self.allowed_trading_days}.",
                    context={"date": eval_ist.date().isoformat(), "day": eval_ist.strftime("%A")},
                )

            # Mandatory square-off boundary (15:10 IST)
            if current_time_ist >= self.mandatory_exit_time:
                return PreTradeValidationResult(
                    is_valid=False,
                    reason_code=RiskRejectReason.MANDATORY_EXIT_PASSED,
                    message=f"New entry rejected: Current time {current_time_ist} is at or past mandatory exit time {self.mandatory_exit_time}.",
                    context={"current_time_ist": str(current_time_ist)},
                )

            # Opening range formation window
            if current_time_ist < self.orb_start_time:
                return PreTradeValidationResult(
                    is_valid=False,
                    reason_code=RiskRejectReason.OUTSIDE_TRADING_HOURS,
                    message=f"Order rejected: Pre-market execution disallowed before {self.orb_start_time} IST.",
                    context={"current_time_ist": str(current_time_ist)},
                )

        # ----------------------------------------------------------------------
        # 6. Instrument & Contract Metadata Validation
        # ----------------------------------------------------------------------
        if contract is None:
            return PreTradeValidationResult(
                is_valid=False,
                reason_code=RiskRejectReason.INVALID_CONTRACT,
                message=f"Order rejected: Target option contract '{order_request.symbol}' could not be resolved from instrument master.",
                context={"symbol": order_request.symbol},
            )

        if contract.lot_size <= 0:
            return PreTradeValidationResult(
                is_valid=False,
                reason_code=RiskRejectReason.INVALID_LOT_SIZE,
                message=f"Order rejected: Contract '{contract.symbol}' has invalid lot size {contract.lot_size}.",
                context={"symbol": contract.symbol, "lot_size": contract.lot_size},
            )

        # ----------------------------------------------------------------------
        # 7. Order Quantity Compliance
        # ----------------------------------------------------------------------
        if order_request.qty <= 0:
            return PreTradeValidationResult(
                is_valid=False,
                reason_code=RiskRejectReason.INVALID_ORDER_QTY,
                message=f"Order rejected: Non-positive quantity ({order_request.qty}).",
                context={"qty": order_request.qty},
            )

        if order_request.qty % contract.lot_size != 0:
            return PreTradeValidationResult(
                is_valid=False,
                reason_code=RiskRejectReason.INVALID_ORDER_QTY,
                message=f"Order rejected: Quantity {order_request.qty} is not an exact multiple of contract lot size {contract.lot_size}.",
                context={"qty": order_request.qty, "lot_size": contract.lot_size},
            )

        # ----------------------------------------------------------------------
        # 8. Supported Order Types
        # ----------------------------------------------------------------------
        if order_request.order_type not in {OrderType.MARKET, OrderType.LIMIT}:
            return PreTradeValidationResult(
                is_valid=False,
                reason_code=RiskRejectReason.UNSUPPORTED_ORDER_TYPE,
                message=f"Order rejected: Order type {order_request.order_type.name} is not supported for options strategy.",
                context={"order_type": order_request.order_type.name},
            )

        # ----------------------------------------------------------------------
        # 9. Market Data Staleness Validation
        # ----------------------------------------------------------------------
        if last_market_data_time is not None:
            age_seconds = (eval_dt - last_market_data_time).total_seconds()
            if age_seconds > self.staleness_threshold_seconds:
                return PreTradeValidationResult(
                    is_valid=False,
                    reason_code=RiskRejectReason.STALE_MARKET_DATA,
                    message=f"Order rejected: Market data is stale ({age_seconds:.1f}s > threshold {self.staleness_threshold_seconds}s).",
                    context={"age_seconds": age_seconds, "threshold": self.staleness_threshold_seconds},
                )

        # ----------------------------------------------------------------------
        # 10. Conflicting In-Flight Order or Position State
        # ----------------------------------------------------------------------
        if is_entry and pending_orders:
            for pending in pending_orders:
                if pending.status in {OrderStatus.NEW, OrderStatus.TRANSIT, OrderStatus.PENDING}:
                    return PreTradeValidationResult(
                        is_valid=False,
                        reason_code=RiskRejectReason.CONFLICTING_ORDER_STATE,
                        message=f"New entry rejected: In-flight order '{pending.client_order_id}' ({pending.status.name}) is pending execution.",
                        context={"pending_order_id": pending.client_order_id, "status": pending.status.name},
                    )

        # If opening a BUY position when already holding an opposing open position
        if is_entry and current_position is not None and current_position.is_open:
            if current_position.symbol != order_request.symbol:
                return PreTradeValidationResult(
                    is_valid=False,
                    reason_code=RiskRejectReason.CONFLICTING_ORDER_STATE,
                    message=f"New entry rejected: Conflicting opposing position '{current_position.symbol}' is still open (NetQty={current_position.net_qty}).",
                    context={"open_symbol": current_position.symbol, "net_qty": current_position.net_qty},
                )

        # ----------------------------------------------------------------------
        # Validation Succeeded
        # ----------------------------------------------------------------------
        self.register_client_order_id(order_request.client_order_id)
        return PreTradeValidationResult(
            is_valid=True,
            message="Order validated successfully by pre-trade gatekeeper.",
            context={"client_order_id": order_request.client_order_id, "symbol": order_request.symbol, "qty": order_request.qty},
        )
