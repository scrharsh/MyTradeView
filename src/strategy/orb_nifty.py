"""NIFTY Opening Range Breakout (ORB) Options Strategy Engine.

Faithfully implements the tested StockMock trading rules:
- Underlying: NIFTY index (NSE:NIFTY50-INDEX).
- Formation Window: 09:16:00 to 09:45:00 IST.
- Trading Days: Monday, Thursday, Friday only.
- Breakout Entries:
    - Upside breakout of OR_HIGH -> Buy ATM weekly CE.
    - Downside breakout of OR_LOW -> Buy ATM weekly PE.
- Atomic Reversals:
    - Holding CE and downside breakout occurs -> Emit CLOSE_CE, transition to REVERSING,
      await confirmed execution fill before emitting ENTER_LONG_PE.
    - Holding PE and upside breakout occurs -> Emit CLOSE_PE, transition to REVERSING,
      await confirmed execution fill before emitting ENTER_LONG_CE.
- Risk Controls:
    - Daily total MTM loss threshold: -₹3,000 (flattens and locks out for session).
    - Profit-protection: +₹3,000 trigger with +₹500 min lock floor (supports lock & trail).
    - Mandatory square-off at 15:10:00 IST.
- Position Size: 1 lot (resolved dynamically from instrument master).
"""

from datetime import date, datetime, time
from decimal import Decimal
import logging
from typing import List, Optional

from config.risk_config import RiskConfig
from config.strategy_config import StrategyConfig
from src.common.constants import (
    BreakoutConfirmationMode,
    ExpiryDaySelectionMode,
    OptionType,
    ProfitTrailingMode,
    RangeBoundaryMode,
    RiskTriggerReason,
    SignalType,
    StrategyState,
)
from src.common.exceptions import InvalidStateTransitionError, UnconfirmedStrategySettingError
from src.common.time_utils import is_trading_day, now_utc, to_ist
from src.core.models import (
    Candle,
    DailyRiskState,
    Fill,
    MarketTick,
    OptionContract,
    Position,
    ProfitProtectionState,
    StrategyExecutionState,
    StrategySignal,
)
from src.data.contract_resolver import ContractResolver
from src.strategy.base import BaseStrategy
from src.strategy.opening_range import OpeningRangeTracker

logger = logging.getLogger(__name__)


class NiftyOrbStrategy(BaseStrategy):
    """
    Event-driven NIFTY ORB Options Strategy Engine.
    
    Adheres strictly to the StockMock specification without introducing unapproved
    optimizations, extra indicators, or unconfirmed parameters.
    """

    def __init__(
        self,
        strategy_config: StrategyConfig,
        risk_config: RiskConfig,
        contract_resolver: Optional[ContractResolver] = None,
    ) -> None:
        self.config = strategy_config
        self.risk_config = risk_config
        self.contract_resolver = contract_resolver

        # Session and execution state
        self._session_id: str = "DEFAULT_SESSION"
        self._session_date: date = date.today()
        self._is_active_today: bool = False
        self._sequence_number: int = 0
        self._last_processed_candle_time: Optional[datetime] = None
        self._last_spot_price: Optional[Decimal] = None

        # Reversal sequencing state
        self._pending_reversal_entry_type: Optional[OptionType] = None
        self._pending_reversal_spot: Optional[Decimal] = None

        # Component state managers
        boundary_mode = self.config.range_boundary_mode or RangeBoundaryMode.UP_TO_0945_CLOSE
        self.range_tracker = OpeningRangeTracker(
            symbol=self.config.underlying_symbol,
            start_time=self.config.orb_start_time,
            end_time=self.config.orb_end_time,
            boundary_mode=boundary_mode,
        )
        self.profit_protection = ProfitProtectionState()
        self.daily_risk = DailyRiskState(
            session_id=self._session_id,
            trading_date=self._session_date,
            max_loss_limit=self.risk_config.max_daily_loss_limit,
        )
        self.execution = StrategyExecutionState(session_id=self._session_id)

    # --------------------------------------------------------------------------
    # Lifecycle: Session Initialization
    # --------------------------------------------------------------------------

    def on_session_start(self, session_id: str, session_date: date) -> None:
        """
        Initialize strategy state for a new trading session.
        Applies trading day filter and resets range and risk trackers.
        """
        self._session_id = session_id
        self._session_date = session_date
        self._sequence_number = 0
        self._last_processed_candle_time = None
        self._last_spot_price = None
        self._pending_reversal_entry_type = None
        self._pending_reversal_spot = None

        # Reinitialize state objects
        boundary_mode = self.config.range_boundary_mode or RangeBoundaryMode.UP_TO_0945_CLOSE
        self.range_tracker = OpeningRangeTracker(
            symbol=self.config.underlying_symbol,
            start_time=self.config.orb_start_time,
            end_time=self.config.orb_end_time,
            boundary_mode=boundary_mode,
        )
        self.range_tracker.reset(session_date)

        self.profit_protection = ProfitProtectionState()
        self.daily_risk = DailyRiskState(
            session_id=session_id,
            trading_date=session_date,
            max_loss_limit=self.risk_config.max_daily_loss_limit,
        )
        self.execution = StrategyExecutionState(
            session_id=session_id,
            current_state=StrategyState.IDLE,
        )

        # Check trading day filter (Monday, Thursday, Friday)
        session_dt = datetime(session_date.year, session_date.month, session_date.day, 9, 15, tzinfo=to_ist(datetime.now()).tzinfo)
        self._is_active_today = is_trading_day(session_dt, self.config.trading_days)

        if not self._is_active_today:
            logger.info(
                f"[Strategy] Session {session_date} ({session_dt.strftime('%A')}) is bypassed. "
                f"Allowed trading days: {self.config.trading_days}"
            )
        else:
            logger.info(f"[Strategy] Session {session_date} initialized. Active trading day.")

    # --------------------------------------------------------------------------
    # Market Data Handlers: Candles & Ticks
    # --------------------------------------------------------------------------

    def on_candle(self, candle: Candle) -> List[StrategySignal]:
        """
        Process a 1-minute candlestick bar.
        
        Evaluates opening range formation, breakout entries, reversals, and mandatory exit.
        """
        if not self._is_active_today:
            return []

        if candle.symbol != self.config.underlying_symbol:
            return []

        # Bar-level deduplication: ignore identical or backwards timestamps
        if self._last_processed_candle_time is not None and candle.timestamp <= self._last_processed_candle_time:
            return []

        self._last_processed_candle_time = candle.timestamp
        self._last_spot_price = candle.close

        candle_ist = to_ist(candle.timestamp)
        bar_time = candle_ist.time()

        # Terminal session states: no further signals permitted
        if self.state in {StrategyState.LOCKED_OUT_FOR_DAY, StrategyState.SQUARED_OFF}:
            return []

        # 1. Mandatory End-of-Day Square-off at 15:10 IST
        if bar_time >= self.config.mandatory_exit_time:
            return self._handle_mandatory_square_off(candle.close, candle.timestamp)

        # 2. Pre-market or before ORB window: ignore
        if bar_time < self.config.orb_start_time:
            return []

        # 3. Opening Range Building Phase (09:16 to 09:45 IST)
        if not self.range_tracker.is_established:
            if self.state == StrategyState.IDLE:
                self._safe_transition(StrategyState.CALCULATING_RANGE, candle.timestamp)

            just_finalized = self.range_tracker.update_from_candle(candle)
            if just_finalized or self.range_tracker.is_established:
                self._safe_transition(StrategyState.RANGE_ESTABLISHED, candle.timestamp)
                self.execution.opening_range = self.range_tracker.to_domain_model()
                logger.info(
                    f"[Strategy] Opening range established: High={self.range_tracker.high}, "
                    f"Low={self.range_tracker.low}, Width={self.range_tracker.range_width} across {self.range_tracker.candle_count} bars."
                )
            else:
                return []

        # 4. Breakout and Reversal Evaluation (Range Established)
        eval_price = candle.close if self.config.breakout_confirmation_mode == BreakoutConfirmationMode.CANDLE_CLOSE else candle.high
        eval_low_price = candle.close if self.config.breakout_confirmation_mode == BreakoutConfirmationMode.CANDLE_CLOSE else candle.low

        return self._evaluate_price_action(eval_price, eval_low_price, candle.timestamp)

    def on_tick(self, tick: MarketTick) -> List[StrategySignal]:
        """
        Process a real-time market tick.
        
        Evaluates range formation or intrabar breakout when configured for intrabar execution.
        """
        if not self._is_active_today:
            return []

        if tick.symbol != self.config.underlying_symbol:
            return []

        self._last_spot_price = tick.ltp
        tick_ist = to_ist(tick.timestamp)
        tick_time = tick_ist.time()

        if self.state in {StrategyState.LOCKED_OUT_FOR_DAY, StrategyState.SQUARED_OFF}:
            return []

        # Mandatory End-of-Day Square-off at 15:10 IST
        if tick_time >= self.config.mandatory_exit_time:
            return self._handle_mandatory_square_off(tick.ltp, tick.timestamp)

        if tick_time < self.config.orb_start_time:
            return []

        # Opening range building
        if not self.range_tracker.is_established:
            if self.state == StrategyState.IDLE:
                self._safe_transition(StrategyState.CALCULATING_RANGE, tick.timestamp)

            just_finalized = self.range_tracker.update_from_tick(tick)
            if just_finalized or self.range_tracker.is_established:
                self._safe_transition(StrategyState.RANGE_ESTABLISHED, tick.timestamp)
                self.execution.opening_range = self.range_tracker.to_domain_model()
            else:
                return []

        # Intrabar tick breakout evaluation
        if self.config.breakout_confirmation_mode == BreakoutConfirmationMode.INTRABAR_TICK:
            return self._evaluate_price_action(tick.ltp, tick.ltp, tick.timestamp)

        return []

    # --------------------------------------------------------------------------
    # Breakout & Reversal Evaluation Core
    # --------------------------------------------------------------------------

    def _evaluate_price_action(
        self,
        high_or_close_price: Decimal,
        low_or_close_price: Decimal,
        timestamp: datetime,
    ) -> List[StrategySignal]:
        """
        Evaluate spot price against range boundaries based on the current strategy state.
        Guarantees zero duplicate signals and enforces two-phase reversal sequencing.
        """
        signals: List[StrategySignal] = []

        # --- A. INITIAL BREAKOUT FROM RANGE_ESTABLISHED ---
        if self.state == StrategyState.RANGE_ESTABLISHED:
            if self.range_tracker.is_broken_above(high_or_close_price):
                contract = self._resolve_contract(OptionType.CE, high_or_close_price)
                signal = self._emit_signal(
                    signal_type=SignalType.ENTER_LONG_CE,
                    underlying_price=high_or_close_price,
                    target_contract=contract,
                    reason=f"NIFTY upside breakout above OR High ({self.range_tracker.high}) at {high_or_close_price}",
                    timestamp=timestamp,
                )
                self.execution.active_contract = contract
                self._safe_transition(StrategyState.LONG_CE, timestamp)
                return [signal]

            elif self.range_tracker.is_broken_below(low_or_close_price):
                contract = self._resolve_contract(OptionType.PE, low_or_close_price)
                signal = self._emit_signal(
                    signal_type=SignalType.ENTER_LONG_PE,
                    underlying_price=low_or_close_price,
                    target_contract=contract,
                    reason=f"NIFTY downside breakout below OR Low ({self.range_tracker.low}) at {low_or_close_price}",
                    timestamp=timestamp,
                )
                self.execution.active_contract = contract
                self._safe_transition(StrategyState.LONG_PE, timestamp)
                return [signal]

        # --- B. HOLDING LONG CE: REVERSAL ON DOWNSIDE BREAKOUT ---
        elif self.state == StrategyState.LONG_CE:
            if self.range_tracker.is_broken_below(low_or_close_price):
                # Transition to REVERSING_CE_TO_PE: Emit CLOSE_CE, DO NOT emit PE yet!
                self._safe_transition(StrategyState.REVERSING_CE_TO_PE, timestamp)
                self._pending_reversal_entry_type = OptionType.PE
                self._pending_reversal_spot = low_or_close_price

                exit_signal = self._emit_signal(
                    signal_type=SignalType.CLOSE_CE,
                    underlying_price=low_or_close_price,
                    target_contract=self.execution.active_contract,
                    reason=f"Reversal triggered: Spot broke below OR Low ({self.range_tracker.low}) at {low_or_close_price}. Closing CE before PE entry.",
                    timestamp=timestamp,
                )
                logger.info(f"[Strategy] Reversal CE -> PE initiated. Exit signal emitted: {exit_signal.signal_id}")
                return [exit_signal]

        # --- C. HOLDING LONG PE: REVERSAL ON UPSIDE BREAKOUT ---
        elif self.state == StrategyState.LONG_PE:
            if self.range_tracker.is_broken_above(high_or_close_price):
                # Transition to REVERSING_PE_TO_CE: Emit CLOSE_PE, DO NOT emit CE yet!
                self._safe_transition(StrategyState.REVERSING_PE_TO_CE, timestamp)
                self._pending_reversal_entry_type = OptionType.CE
                self._pending_reversal_spot = high_or_close_price

                exit_signal = self._emit_signal(
                    signal_type=SignalType.CLOSE_PE,
                    underlying_price=high_or_close_price,
                    target_contract=self.execution.active_contract,
                    reason=f"Reversal triggered: Spot broke above OR High ({self.range_tracker.high}) at {high_or_close_price}. Closing PE before CE entry.",
                    timestamp=timestamp,
                )
                logger.info(f"[Strategy] Reversal PE -> CE initiated. Exit signal emitted: {exit_signal.signal_id}")
                return [exit_signal]

        # --- D. IN TRANSIT / REVERSING: AWAITING FILL CONFIRMATION ---
        elif self.state in {StrategyState.REVERSING_CE_TO_PE, StrategyState.REVERSING_PE_TO_CE}:
            # Suppress all new signals until exit fill arrives
            return []

        return signals

    # --------------------------------------------------------------------------
    # Order Fill Handler: Confirmed Execution & Reversal Phase 2
    # --------------------------------------------------------------------------

    def on_order_fill(self, fill: Fill) -> List[StrategySignal]:
        """
        Process confirmed execution fill report.
        
        When in a REVERSING state, confirming the exit fill triggers the replacement entry.
        """
        signals: List[StrategySignal] = []

        if self.state == StrategyState.REVERSING_CE_TO_PE:
            if self._pending_reversal_entry_type == OptionType.PE:
                spot_price = self._pending_reversal_spot or self._last_spot_price or Decimal("24500.00")
                contract = self._resolve_contract(OptionType.PE, spot_price)

                entry_signal = self._emit_signal(
                    signal_type=SignalType.ENTER_LONG_PE,
                    underlying_price=spot_price,
                    target_contract=contract,
                    reason=f"Reversal replacement entry: CE exit confirmed filled at {fill.fill_price}. Opening PE.",
                    timestamp=fill.timestamp,
                )
                self.execution.active_contract = contract
                self._pending_reversal_entry_type = None
                self._pending_reversal_spot = None
                self._safe_transition(StrategyState.LONG_PE, fill.timestamp)

                logger.info(f"[Strategy] Reversal completed: Long PE entered ({contract.symbol})")
                return [entry_signal]

        elif self.state == StrategyState.REVERSING_PE_TO_CE:
            if self._pending_reversal_entry_type == OptionType.CE:
                spot_price = self._pending_reversal_spot or self._last_spot_price or Decimal("24500.00")
                contract = self._resolve_contract(OptionType.CE, spot_price)

                entry_signal = self._emit_signal(
                    signal_type=SignalType.ENTER_LONG_CE,
                    underlying_price=spot_price,
                    target_contract=contract,
                    reason=f"Reversal replacement entry: PE exit confirmed filled at {fill.fill_price}. Opening CE.",
                    timestamp=fill.timestamp,
                )
                self.execution.active_contract = contract
                self._pending_reversal_entry_type = None
                self._pending_reversal_spot = None
                self._safe_transition(StrategyState.LONG_CE, fill.timestamp)

                logger.info(f"[Strategy] Reversal completed: Long CE entered ({contract.symbol})")
                return [entry_signal]

        return signals

    # --------------------------------------------------------------------------
    # Risk Management & Real-time MTM Evaluation
    # --------------------------------------------------------------------------

    def on_position_update(self, position: Position) -> List[StrategySignal]:
        """Process updated position state and evaluate MTM boundaries."""
        self.execution.active_position = position
        return self.on_mtm_update(
            realized_pnl=position.realized_pnl,
            unrealized_pnl=position.unrealized_pnl,
            timestamp=position.closed_at or position.opened_at,
        )

    def on_mtm_update(
        self,
        realized_pnl: Decimal,
        unrealized_pnl: Decimal,
        timestamp: Optional[datetime] = None,
    ) -> List[StrategySignal]:
        """
        Evaluate real-time MTM against daily loss limit and profit protection triggers.
        """
        ts = timestamp or now_utc()
        self.daily_risk.update_mtm(realized_pnl, unrealized_pnl)

        # 1. Hard Daily Strategy Loss Limit Trigger (-₹3,000)
        if self.daily_risk.is_kill_switch_active or self.daily_risk.total_mtm <= -abs(self.risk_config.max_daily_loss_limit):
            if self.state not in {StrategyState.LOCKED_OUT_FOR_DAY, StrategyState.SQUARED_OFF}:
                logger.critical(
                    f"[Strategy RISK] Daily loss threshold breached! Total MTM: ₹{self.daily_risk.total_mtm} "
                    f"<= Limit: -₹{self.risk_config.max_daily_loss_limit}. Initiating emergency lockout."
                )
                self.daily_risk.trigger_kill_switch(RiskTriggerReason.DAILY_LOSS_LIMIT_BREACHED, ts)
                signals = self._force_flatten_all_positions(
                    reason=f"Daily MTM loss limit breached (MTM: ₹{self.daily_risk.total_mtm})",
                    timestamp=ts,
                )
                self._safe_transition(StrategyState.LOCKED_OUT_FOR_DAY, ts)
                return signals

        # 2. Profit Protection Trigger (+₹3,000 trigger with +₹500 min lock floor)
        trailing_mode = self.risk_config.profit_trailing_mode or ProfitTrailingMode.LOCK_MINIMUM_ONLY
        floor_breached = self.profit_protection.evaluate(
            current_mtm=self.daily_risk.total_mtm,
            trigger_profit=self.risk_config.profit_protection_trigger,
            min_lock=self.risk_config.profit_protection_min_lock,
            step_profit=self.risk_config.profit_trailing_step_profit,
            step_lock=self.risk_config.profit_trailing_step_lock,
            mode=trailing_mode.value,
        )

        if floor_breached:
            if self.state in {StrategyState.LONG_CE, StrategyState.LONG_PE, StrategyState.REVERSING_CE_TO_PE, StrategyState.REVERSING_PE_TO_CE}:
                logger.warning(
                    f"[Strategy RISK] Profit protection floor breached! Peak MTM: ₹{self.profit_protection.peak_mtm}, "
                    f"Floor: ₹{self.profit_protection.current_floor}, Current MTM: ₹{self.daily_risk.total_mtm}. Exiting positions."
                )
                exit_signals = self._force_flatten_all_positions(
                    reason=f"Profit protection floor breached (MTM: ₹{self.daily_risk.total_mtm} <= Floor: ₹{self.profit_protection.current_floor})",
                    timestamp=ts,
                )
                self._safe_transition(StrategyState.PROFIT_LOCKED, ts)
                return exit_signals

        return []

    def on_risk_event(
        self,
        reason: RiskTriggerReason,
        timestamp: Optional[datetime] = None,
    ) -> List[StrategySignal]:
        """Process external risk circuit breaker trigger."""
        ts = timestamp or now_utc()
        self.daily_risk.trigger_kill_switch(reason, ts)
        signals = self._force_flatten_all_positions(
            reason=f"External risk trigger: {reason.value}",
            timestamp=ts,
        )
        self._safe_transition(StrategyState.LOCKED_OUT_FOR_DAY, ts)
        return signals

    # --------------------------------------------------------------------------
    # Mandatory Square-Off & EOD Cleanup
    # --------------------------------------------------------------------------

    def _handle_mandatory_square_off(self, spot_price: Decimal, timestamp: datetime) -> List[StrategySignal]:
        """Unconditionally square off all strategy positions at 15:10 IST."""
        signals: List[StrategySignal] = []

        if self.state in {StrategyState.LONG_CE, StrategyState.LONG_PE, StrategyState.REVERSING_CE_TO_PE, StrategyState.REVERSING_PE_TO_CE}:
            logger.info(f"[Strategy] Mandatory 15:10 IST square-off triggered at spot {spot_price}.")
            signals = self._force_flatten_all_positions(
                reason="Mandatory intraday EOD square-off at 15:10:00 IST",
                timestamp=timestamp,
            )

        self._safe_transition(StrategyState.SQUARED_OFF, timestamp)
        return signals

    def on_session_end(self, timestamp: Optional[datetime] = None) -> List[StrategySignal]:
        """Finalize session at 15:30 IST or market close."""
        ts = timestamp or now_utc()
        if self.state not in {StrategyState.SQUARED_OFF, StrategyState.LOCKED_OUT_FOR_DAY}:
            signals = self._force_flatten_all_positions(reason="Session finalization", timestamp=ts)
            self._safe_transition(StrategyState.SQUARED_OFF, ts)
            return signals
        return []

    # --------------------------------------------------------------------------
    # Helper & Utility Methods
    # --------------------------------------------------------------------------

    def _emit_signal(
        self,
        signal_type: SignalType,
        underlying_price: Decimal,
        target_contract: Optional[OptionContract],
        reason: str,
        timestamp: datetime,
    ) -> StrategySignal:
        """Create an immutable, sequenced StrategySignal."""
        self._sequence_number += 1
        signal = StrategySignal(
            signal_id=f"SIG_{self._session_id}_{self._sequence_number:04d}",
            session_id=self._session_id,
            timestamp=timestamp,
            signal_type=signal_type,
            underlying_price=underlying_price,
            target_contract=target_contract,
            reason=reason,
            sequence_number=self._sequence_number,
        )
        self.execution.last_signal = signal
        return signal

    def _resolve_contract(self, option_type: OptionType, spot_price: Decimal) -> OptionContract:
        """Resolve authoritative ATM option contract."""
        if self.contract_resolver is None:
            # Fallback mock contract for standalone testing if resolver not injected
            atm_strike = (spot_price / Decimal("50.00")).quantize(Decimal("1")) * Decimal("50.00")
            return OptionContract(
                symbol=f"NSE:NIFTY{self._session_date.strftime('%y%b').upper()}{atm_strike:.0f}{option_type.value}",
                underlying=self.config.underlying_symbol,
                strike=atm_strike,
                option_type=option_type,
                expiry_date=self._session_date,
                lot_size=self.config.lot_size,
            )

        expiry_mode = self.config.expiry_day_selection or ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE
        return self.contract_resolver.resolve_atm_contract(
            underlying=self.config.underlying_symbol,
            spot_price=spot_price,
            option_type=option_type,
            session_date=self._session_date,
            expiry_day_mode=expiry_mode,
        )

    def _force_flatten_all_positions(self, reason: str, timestamp: datetime) -> List[StrategySignal]:
        """Emit square-off signal to close open strategy exposure."""
        signals: List[StrategySignal] = []
        spot = self._last_spot_price or Decimal("24500.00")

        if self.state in {StrategyState.LONG_CE, StrategyState.REVERSING_PE_TO_CE}:
            signals.append(self._emit_signal(
                signal_type=SignalType.CLOSE_CE,
                underlying_price=spot,
                target_contract=self.execution.active_contract,
                reason=reason,
                timestamp=timestamp,
            ))
        elif self.state in {StrategyState.LONG_PE, StrategyState.REVERSING_CE_TO_PE}:
            signals.append(self._emit_signal(
                signal_type=SignalType.CLOSE_PE,
                underlying_price=spot,
                target_contract=self.execution.active_contract,
                reason=reason,
                timestamp=timestamp,
            ))
        else:
            # Emit generic force square-off if active contract exists
            if self.execution.active_contract is not None:
                signals.append(self._emit_signal(
                    signal_type=SignalType.FORCE_SQUARE_OFF,
                    underlying_price=spot,
                    target_contract=self.execution.active_contract,
                    reason=reason,
                    timestamp=timestamp,
                ))

        self.execution.active_contract = None
        self._pending_reversal_entry_type = None
        self._pending_reversal_spot = None
        return signals

    def _safe_transition(self, target: StrategyState, timestamp: datetime) -> None:
        """Safely apply state transition using domain state machine."""
        if self.execution.current_state == target:
            return
        self.execution.transition_to(target, timestamp)

    @property
    def state(self) -> StrategyState:
        """Current state of strategy instance."""
        return self.execution.current_state

    @property
    def execution_state(self) -> StrategyExecutionState:
        """Snapshot of execution state."""
        return self.execution
