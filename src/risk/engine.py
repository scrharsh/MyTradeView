"""Unified Risk Management Engine Facade for MyTradeView.

Aggregates MtmTracker, DailyLossCircuitBreaker, ProfitProtectionEngine,
PreTradeGatekeeper, and KillSwitchCoordinator into a cohesive, decoupled risk subsystem.
"""

from datetime import datetime, time
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from config.risk_config import RiskConfig
from config.strategy_config import StrategyConfig
from src.common.constants import (
    ExecutionMode,
    ProfitTrailingMode,
    RiskRejectReason,
    RiskTriggerReason,
)
from src.core.models import (
    BrokerOrder,
    Fill,
    OptionContract,
    OrderRequest,
    Position,
    TradingSession,
)
from src.risk.base import PreTradeValidationResult, RiskSnapshot
from src.risk.circuit_breaker import DailyLossCircuitBreaker
from src.risk.kill_switch import KillSwitchCoordinator
from src.risk.pre_trade import PreTradeGatekeeper
from src.risk.profit_protection import ProfitProtectionEngine
from src.risk.tracker import MtmTracker

logger = logging.getLogger(__name__)


class RiskEngine:
    """
    Central Risk Engine orchestrating all Tier-1 and Tier-2 risk controls.
    
    Operates independently of strategy engines, providing modular, reusable
    risk containment for any current or future strategy template.
    """

    def __init__(
        self,
        session_id: str,
        risk_config: RiskConfig,
        strategy_config: Optional[StrategyConfig] = None,
        staleness_threshold_seconds: int = 60,
    ) -> None:
        self.session_id = session_id
        self.risk_config = risk_config
        self.strategy_config = strategy_config

        # 1. Standalone MTM Tracker
        self.tracker = MtmTracker(
            session_id=session_id,
            staleness_threshold_seconds=staleness_threshold_seconds,
        )

        # 2. Daily Loss Circuit Breaker (-₹3,000 threshold)
        self.circuit_breaker = DailyLossCircuitBreaker(
            max_daily_loss=risk_config.max_daily_loss_limit,
        )

        # 3. Profit Protection Engine (+₹3,000 trigger / +₹500 floor)
        trailing_mode = risk_config.profit_trailing_mode or ProfitTrailingMode.LOCK_AND_TRAIL
        self.profit_protection = ProfitProtectionEngine(
            trigger_profit=risk_config.profit_protection_trigger,
            min_lock=risk_config.profit_protection_min_lock,
            step_profit=risk_config.profit_trailing_step_profit,
            step_lock=risk_config.profit_trailing_step_lock,
            mode=trailing_mode,
        )

        # 4. Central Pre-Trade Risk Gatekeeper
        allowed_days = strategy_config.trading_days if strategy_config else ["Monday", "Thursday", "Friday"]
        orb_start = strategy_config.orb_start_time if strategy_config else time(9, 16, 0)
        mandatory_exit = strategy_config.mandatory_exit_time if strategy_config else time(15, 10, 0)

        self.gatekeeper = PreTradeGatekeeper(
            allowed_trading_days=allowed_days,
            orb_start_time=orb_start,
            mandatory_exit_time=mandatory_exit,
            staleness_threshold_seconds=staleness_threshold_seconds,
        )

        # 5. Centralized Kill-Switch Coordinator
        self.kill_switch = KillSwitchCoordinator(session_id=session_id)

    # --------------------------------------------------------------------------
    # Tier 1: Pre-Trade Gatekeeping
    # --------------------------------------------------------------------------

    def validate_order(
        self,
        order_request: OrderRequest,
        execution_mode: ExecutionMode,
        session: TradingSession,
        contract: Optional[OptionContract],
        current_position: Optional[Position] = None,
        pending_orders: Optional[List[BrokerOrder]] = None,
        last_market_data_time: Optional[datetime] = None,
        evaluation_time: Optional[datetime] = None,
    ) -> PreTradeValidationResult:
        """
        Validate proposed order through the pre-trade gatekeeper.
        
        Evaluates execution mode, trading session, daily loss lockout, profit floor,
        contract validity, quantity compliance, data freshness, and conflicting states.
        """
        is_loss_locked = self.circuit_breaker.is_locked_out or self.kill_switch.is_active
        is_profit_locked = self.profit_protection.status.name in {"EXIT_REQUESTED", "EXIT_CONFIRMED"}

        return self.gatekeeper.validate_order_request(
            order_request=order_request,
            execution_mode=execution_mode,
            session=session,
            contract=contract,
            is_kill_switch_active=is_loss_locked,
            is_profit_protection_locked=is_profit_locked,
            current_position=current_position,
            pending_orders=pending_orders,
            last_market_data_time=last_market_data_time,
            evaluation_time=evaluation_time,
        )

    # --------------------------------------------------------------------------
    # Tier 2: Real-time MTM & In-Trade Risk Monitoring
    # --------------------------------------------------------------------------

    def on_market_price_update(
        self,
        symbol: str,
        ltp: Decimal,
        timestamp: Optional[datetime] = None,
        open_positions: Optional[List[Position]] = None,
    ) -> RiskSnapshot:
        """
        Process price update, recalculate MTM, and evaluate circuit breakers.
        """
        self.tracker.update_market_price(symbol, ltp, timestamp)
        current_mtm = self.tracker.total_mtm

        # Evaluate Hard Daily Loss Circuit Breaker
        loss_breached = self.circuit_breaker.evaluate(current_mtm, open_positions, timestamp)
        if loss_breached and not self.kill_switch.is_active:
            self.kill_switch.trigger(
                reason=RiskTriggerReason.DAILY_LOSS_LIMIT_BREACHED,
                details={"breach_mtm": str(current_mtm), "loss_limit": str(self.risk_config.max_daily_loss_limit)},
                timestamp=timestamp,
            )

        # Evaluate Profit-Protection Floor
        profit_breached = self.profit_protection.evaluate(current_mtm, timestamp)
        if profit_breached:
            self.tracker.record_risk_event("PROFIT_PROTECTION_BREACH", {
                "mtm": str(current_mtm),
                "peak_mtm": str(self.profit_protection.peak_mtm),
                "floor": str(self.profit_protection.current_floor),
            })

        return self.get_snapshot()

    def on_fill(self, fill: Fill) -> RiskSnapshot:
        """Process execution fill report and update risk accounting."""
        self.tracker.on_fill(fill)

        # If a position closure fill was confirmed, notify circuit breaker and kill switch
        if fill.side.name == "SELL":
            self.circuit_breaker.confirm_position_closed(fill.symbol)
            self.kill_switch.confirm_position_closed(fill.symbol)

        return self.get_snapshot()

    def trigger_emergency_kill_switch(
        self,
        reason: RiskTriggerReason,
        open_positions: List[Position],
        pending_orders: List[BrokerOrder],
        details: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[str], List[str]]:
        """
        Trigger emergency kill switch and coordinate life-safety cancellations and closures.
        
        Returns:
            Tuple of (order_ids_to_cancel, position_symbols_to_flatten).
        """
        self.kill_switch.trigger(reason, details)
        return self.kill_switch.coordinate_emergency_actions(open_positions, pending_orders)

    # --------------------------------------------------------------------------
    # Status & Snapshot Queries
    # --------------------------------------------------------------------------

    @property
    def can_trade(self) -> bool:
        """True only if both the circuit breaker and kill-switch permit trading."""
        return self.circuit_breaker.can_trade and self.kill_switch.can_trade

    def get_snapshot(self) -> RiskSnapshot:
        """Produce an immutable snapshot of current risk metrics."""
        return self.tracker.get_snapshot(
            is_kill_switch_active=self.kill_switch.is_active or self.circuit_breaker.is_locked_out,
            is_profit_protection_active=self.profit_protection.is_activated,
            profit_floor=self.profit_protection.current_floor,
        )

    def reset_for_new_session(self, new_session_id: str) -> None:
        """Reset risk subsystem for a new trading day."""
        self.session_id = new_session_id
        self.tracker = MtmTracker(session_id=new_session_id)
        self.circuit_breaker.reset_for_new_session()
        self.profit_protection.reset_for_new_session()
        self.kill_switch.reset_for_new_session(new_session_id)
