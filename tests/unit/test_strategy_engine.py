"""Comprehensive unit tests for NIFTY ORB Strategy Engine, Range Tracker, and StockMock fidelity."""

from datetime import date, datetime, time, timezone
from decimal import Decimal
import pytest
import pytz

from config.risk_config import RiskConfig
from config.strategy_config import StrategyConfig
from src.common.constants import (
    BreakoutConfirmationMode,
    ExpiryDaySelectionMode,
    OptionType,
    OrderSide,
    ProfitTrailingMode,
    RangeBoundaryMode,
    RiskTriggerReason,
    SignalType,
    StrategyState,
)
from src.core.models import (
    Candle,
    Fill,
    MarketTick,
    OptionContract,
)
from src.data.contract_resolver import ContractResolver
from src.data.instrument_master import InstrumentMasterRegistry
from src.strategy.opening_range import OpeningRangeTracker
from src.strategy.orb_nifty import NiftyOrbStrategy

IST = pytz.timezone("Asia/Kolkata")


def make_candle(
    time_str: str,
    open_p: str,
    high_p: str,
    low_p: str,
    close_p: str,
    date_str: str = "2026-11-06",  # Friday
    symbol: str = "NSE:NIFTY50-INDEX",
) -> Candle:
    """Helper to construct timezone-aware UTC Candle from IST strings."""
    dt_naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    dt_ist = IST.localize(dt_naive)
    dt_utc = dt_ist.astimezone(timezone.utc)
    return Candle(
        symbol=symbol,
        timestamp=dt_utc,
        open=Decimal(open_p),
        high=Decimal(high_p),
        low=Decimal(low_p),
        close=Decimal(close_p),
        volume=1000,
    )


def make_tick(
    time_str: str,
    ltp: str,
    date_str: str = "2026-11-06",
    symbol: str = "NSE:NIFTY50-INDEX",
) -> MarketTick:
    """Helper to construct timezone-aware UTC MarketTick from IST strings."""
    dt_naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    dt_ist = IST.localize(dt_naive)
    dt_utc = dt_ist.astimezone(timezone.utc)
    return MarketTick(
        symbol=symbol,
        timestamp=dt_utc,
        ltp=Decimal(ltp),
        volume=100,
    )


@pytest.fixture
def mock_contract_resolver() -> ContractResolver:
    """Provide a contract resolver backed by a realistic synthetic instrument master."""
    csv_data = """trading_symbol,underlying_symbol,strike_price,option_type,expiry_date,minimum_lot_size,tick_size
NSE:NIFTY26N0624500CE,NSE:NIFTY50-INDEX,24500.0,CE,2026-11-06,25,0.05
NSE:NIFTY26N0624500PE,NSE:NIFTY50-INDEX,24500.0,PE,2026-11-06,25,0.05
NSE:NIFTY26N0624550CE,NSE:NIFTY50-INDEX,24550.0,CE,2026-11-06,25,0.05
NSE:NIFTY26N0624550PE,NSE:NIFTY50-INDEX,24550.0,PE,2026-11-06,25,0.05
NSE:NIFTY26N0624600CE,NSE:NIFTY50-INDEX,24600.0,CE,2026-11-06,25,0.05
NSE:NIFTY26N0624600PE,NSE:NIFTY50-INDEX,24600.0,PE,2026-11-06,25,0.05
NSE:NIFTY26N0624350PE,NSE:NIFTY50-INDEX,24350.0,PE,2026-11-06,25,0.05
NSE:NIFTY26N0624400PE,NSE:NIFTY50-INDEX,24400.0,PE,2026-11-06,25,0.05
NSE:NIFTY26N0624400CE,NSE:NIFTY50-INDEX,24400.0,CE,2026-11-06,25,0.05
"""
    registry = InstrumentMasterRegistry()
    registry.parse_csv(csv_data)
    return ContractResolver(registry)


@pytest.fixture
def standard_strategy(mock_contract_resolver: ContractResolver) -> NiftyOrbStrategy:
    """Configured strategy instance for testing."""
    strat_cfg = StrategyConfig(
        underlying_symbol="NSE:NIFTY50-INDEX",
        orb_start_time=time(9, 16, 0),
        orb_end_time=time(9, 45, 0),
        mandatory_exit_time=time(15, 10, 0),
        trading_days=["Monday", "Thursday", "Friday"],
        breakout_confirmation_mode=BreakoutConfirmationMode.CANDLE_CLOSE,
        range_boundary_mode=RangeBoundaryMode.UP_TO_0944_CLOSE,
        expiry_day_selection=ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE,
    )
    risk_cfg = RiskConfig(
        max_daily_loss_limit=Decimal("3000.00"),
        profit_protection_trigger=Decimal("3000.00"),
        profit_protection_min_lock=Decimal("500.00"),
        profit_trailing_mode=ProfitTrailingMode.LOCK_AND_TRAIL,
        profit_trailing_step_profit=Decimal("1000.00"),
        profit_trailing_step_lock=Decimal("500.00"),
    )
    strategy = NiftyOrbStrategy(
        strategy_config=strat_cfg,
        risk_config=risk_cfg,
        contract_resolver=mock_contract_resolver,
    )
    strategy.on_session_start("SESS_20261106", date(2026, 11, 6))  # Friday
    return strategy


# ==============================================================================
# 1. Opening Range Tracker & Boundary Modes
# ==============================================================================

def test_opening_range_accumulation_and_boundary_modes():
    """Verify range formation between 09:16 and 09:45 IST and boundary freezing."""
    tracker = OpeningRangeTracker(
        symbol="NSE:NIFTY50-INDEX",
        start_time=time(9, 16, 0),
        end_time=time(9, 45, 0),
        boundary_mode=RangeBoundaryMode.UP_TO_0944_CLOSE,
    )
    tracker.reset(date(2026, 11, 6))

    # Pre-market candle (09:15) should be ignored
    c_0915 = make_candle("09:15:00", "24450", "24480", "24440", "24460")
    tracker.update_from_candle(c_0915)
    assert tracker.candle_count == 0
    assert not tracker.is_established

    # 09:16 candle
    c_0916 = make_candle("09:16:00", "24460", "24500", "24450", "24490")
    tracker.update_from_candle(c_0916)
    assert tracker.candle_count == 1
    assert tracker.high == Decimal("24500")
    assert tracker.low == Decimal("24450")

    # 09:25 candle with higher high and lower low
    c_0925 = make_candle("09:25:00", "24490", "24520", "24430", "24480")
    tracker.update_from_candle(c_0925)
    assert tracker.candle_count == 2
    assert tracker.high == Decimal("24520")
    assert tracker.low == Decimal("24430")

    # 09:44 candle (final bar in formation window)
    c_0944 = make_candle("09:44:00", "24480", "24510", "24460", "24500")
    tracker.update_from_candle(c_0944)
    assert not tracker.is_established

    # 09:45 candle triggers finalization for UP_TO_0944_CLOSE
    c_0945 = make_candle("09:45:00", "24500", "24560", "24490", "24550")
    just_finalized = tracker.update_from_candle(c_0945)
    assert just_finalized
    assert tracker.is_established
    assert tracker.high == Decimal("24520")
    assert tracker.low == Decimal("24430")
    assert tracker.range_width == Decimal("90")

    # Subsequent candles do NOT modify range boundaries
    c_0946 = make_candle("09:46:00", "24550", "24600", "24400", "24590")
    tracker.update_from_candle(c_0946)
    assert tracker.high == Decimal("24520")
    assert tracker.low == Decimal("24430")


def test_active_opening_range_includes_0945_and_starts_breakout_evaluation_at_0946():
    """The active mode requires all 30 completed bars from 09:16 through 09:45."""
    tracker = OpeningRangeTracker(
        symbol="NSE:NIFTY50-INDEX",
        start_time=time(9, 16, 0),
        end_time=time(9, 45, 0),
        boundary_mode=RangeBoundaryMode.UP_TO_0945_CLOSE,
    )
    tracker.reset(date(2026, 11, 6))

    for minute in range(16, 46):
        high = "24550" if minute == 45 else "24500"
        tracker.update_from_candle(make_candle(f"09:{minute:02d}:00", "24450", high, "24400", "24450"))

    assert tracker.candle_count == 30
    assert tracker.is_established is False
    assert tracker.update_from_candle(make_candle("09:46:00", "24450", "24600", "24300", "24550")) is True
    assert tracker.is_established is True
    assert tracker.high == Decimal("24550")
    assert tracker.low == Decimal("24400")


def test_active_opening_range_rejects_incomplete_data_before_breakout():
    """A missing opening-range minute cannot establish the range or trigger trading."""
    tracker = OpeningRangeTracker(
        symbol="NSE:NIFTY50-INDEX",
        start_time=time(9, 16, 0),
        end_time=time(9, 45, 0),
        boundary_mode=RangeBoundaryMode.UP_TO_0945_CLOSE,
    )
    tracker.reset(date(2026, 11, 6))

    for minute in range(16, 46):
        if minute != 30:
            tracker.update_from_candle(make_candle(f"09:{minute:02d}:00", "24450", "24500", "24400", "24450"))

    assert tracker.candle_count == 29
    assert tracker.update_from_candle(make_candle("09:46:00", "24450", "24600", "24300", "24550")) is False
    assert tracker.is_established is False


def test_active_opening_range_duplicate_replacing_missing_minute_does_not_establish():
    """A duplicate cannot substitute for a missing expected opening-range minute."""
    tracker = OpeningRangeTracker(boundary_mode=RangeBoundaryMode.UP_TO_0945_CLOSE)
    tracker.reset(date(2026, 11, 6))

    for minute in range(16, 46):
        if minute != 30:
            tracker.update_from_candle(make_candle(f"09:{minute:02d}:00", "24450", "24500", "24400", "24450"))
    tracker.update_from_candle(make_candle("09:29:00", "24450", "24600", "24400", "24450"))

    assert tracker.candle_count == 29
    assert tracker.update_from_candle(make_candle("09:46:00", "24450", "24600", "24300", "24550")) is False
    assert tracker.is_established is False


def test_active_opening_range_duplicate_0945_does_not_increase_count():
    """Duplicate delivery of the final range candle is idempotent."""
    tracker = OpeningRangeTracker(boundary_mode=RangeBoundaryMode.UP_TO_0945_CLOSE)
    tracker.reset(date(2026, 11, 6))

    for minute in range(16, 46):
        tracker.update_from_candle(make_candle(f"09:{minute:02d}:00", "24450", "24500", "24400", "24450"))
    tracker.update_from_candle(make_candle("09:45:00", "24450", "24600", "24400", "24450"))

    assert tracker.candle_count == 30
    assert tracker.update_from_candle(make_candle("09:46:00", "24450", "24600", "24300", "24550")) is True
    assert tracker.is_established is True


def test_active_opening_range_accepts_out_of_order_unique_candles():
    """Out-of-order delivery does not lose timestamps or corrupt range bounds."""
    tracker = OpeningRangeTracker(boundary_mode=RangeBoundaryMode.UP_TO_0945_CLOSE)
    tracker.reset(date(2026, 11, 6))

    for minute in reversed(range(16, 46)):
        high = "24550" if minute == 45 else "24500"
        tracker.update_from_candle(make_candle(f"09:{minute:02d}:00", "24450", high, "24400", "24450"))

    assert tracker.candle_count == 30
    assert tracker.update_from_candle(make_candle("09:46:00", "24450", "24600", "24300", "24550")) is True
    assert tracker.high == Decimal("24550")
    assert tracker.low == Decimal("24400")


def test_incomplete_active_range_cannot_emit_breakout_entry():
    """The strategy must remain in range calculation until all 30 bars exist."""
    strategy = NiftyOrbStrategy(
        StrategyConfig(
            underlying_symbol="NSE:NIFTY50-INDEX",
            orb_start_time=time(9, 16, 0),
            orb_end_time=time(9, 45, 0),
            trading_days=["Friday"],
            breakout_confirmation_mode=BreakoutConfirmationMode.CANDLE_CLOSE,
            range_boundary_mode=RangeBoundaryMode.UP_TO_0945_CLOSE,
            expiry_day_selection=ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE,
        ),
        RiskConfig(),
    )
    strategy.on_session_start("SESS_INCOMPLETE_OR", date(2026, 11, 6))

    for minute in range(16, 46):
        if minute != 30:
            strategy.on_candle(make_candle(f"09:{minute:02d}:00", "24450", "24500", "24400", "24450"))

    assert strategy.on_candle(make_candle("09:46:00", "24450", "24600", "24300", "24550")) == []
    assert strategy.state == StrategyState.CALCULATING_RANGE


# ==============================================================================
# 2. Trading Day Filter
# ==============================================================================

def test_trading_day_filter(mock_contract_resolver: ContractResolver):
    """Verify that only Monday, Thursday, Friday are traded; Tue, Wed, Sat, Sun are bypassed."""
    strat_cfg = StrategyConfig(
        underlying_symbol="NSE:NIFTY50-INDEX",
        trading_days=["Monday", "Thursday", "Friday"],
        breakout_confirmation_mode=BreakoutConfirmationMode.CANDLE_CLOSE,
        range_boundary_mode=RangeBoundaryMode.UP_TO_0944_CLOSE,
        expiry_day_selection=ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE,
    )
    risk_cfg = RiskConfig(
        profit_trailing_mode=ProfitTrailingMode.LOCK_AND_TRAIL,
    )
    strat = NiftyOrbStrategy(strat_cfg, risk_cfg, mock_contract_resolver)

    # Tuesday: 2026-11-03
    strat.on_session_start("SESS_TUE", date(2026, 11, 3))
    assert not strat._is_active_today
    c = make_candle("09:46:00", "24500", "24600", "24400", "24580", date_str="2026-11-03")
    signals = strat.on_candle(c)
    assert len(signals) == 0
    assert strat.state == StrategyState.IDLE

    # Wednesday: 2026-11-04
    strat.on_session_start("SESS_WED", date(2026, 11, 4))
    assert not strat._is_active_today

    # Thursday: 2026-11-05
    strat.on_session_start("SESS_THU", date(2026, 11, 5))
    assert strat._is_active_today

    # Friday: 2026-11-06
    strat.on_session_start("SESS_FRI", date(2026, 11, 6))
    assert strat._is_active_today


# ==============================================================================
# 3. Initial Breakout Entries: CE & PE
# ==============================================================================

def test_initial_upside_breakout_generates_long_ce(standard_strategy: NiftyOrbStrategy):
    """Verify that spot breaking above OR_HIGH generates an ENTER_LONG_CE signal with ATM strike."""
    strat = standard_strategy

    # Build range 09:16 to 09:44: High = 24500, Low = 24400
    c1 = make_candle("09:16:00", "24450", "24500", "24420", "24460")
    c2 = make_candle("09:30:00", "24460", "24480", "24400", "24450")
    assert strat.on_candle(c1) == []
    assert strat.on_candle(c2) == []
    assert strat.state == StrategyState.CALCULATING_RANGE

    # 09:45 candle establishes range (High=24500, Low=24400), close is inside range
    c_final = make_candle("09:45:00", "24450", "24490", "24440", "24480")
    assert strat.on_candle(c_final) == []
    assert strat.state == StrategyState.RANGE_ESTABLISHED
    assert strat.range_tracker.high == Decimal("24500")
    assert strat.range_tracker.low == Decimal("24400")

    # 09:46 candle breaks out upward: Close=24550 > OR_HIGH (24500)
    c_breakout = make_candle("09:46:00", "24480", "24560", "24470", "24550")
    signals = strat.on_candle(c_breakout)

    assert len(signals) == 1
    sig = signals[0]
    assert sig.signal_type == SignalType.ENTER_LONG_CE
    assert sig.underlying_price == Decimal("24550")
    assert sig.target_contract is not None
    assert sig.target_contract.option_type == OptionType.CE
    assert sig.target_contract.strike == Decimal("24550")
    assert sig.target_contract.lot_size == 25
    assert sig.sequence_number == 1
    assert strat.state == StrategyState.LONG_CE


def test_initial_downside_breakout_generates_long_pe(standard_strategy: NiftyOrbStrategy):
    """Verify that spot breaking below OR_LOW generates an ENTER_LONG_PE signal with ATM strike."""
    strat = standard_strategy

    # Build range: High = 24500, Low = 24400
    strat.on_candle(make_candle("09:16:00", "24450", "24500", "24400", "24450"))
    strat.on_candle(make_candle("09:45:00", "24450", "24480", "24420", "24450"))
    assert strat.state == StrategyState.RANGE_ESTABLISHED

    # 09:46 candle breaks out downward: Close=24350 < OR_LOW (24400)
    c_breakout = make_candle("09:46:00", "24450", "24460", "24340", "24350")
    signals = strat.on_candle(c_breakout)

    assert len(signals) == 1
    sig = signals[0]
    assert sig.signal_type == SignalType.ENTER_LONG_PE
    assert sig.underlying_price == Decimal("24350")
    assert sig.target_contract is not None
    assert sig.target_contract.option_type == OptionType.PE
    assert sig.target_contract.strike == Decimal("24350")
    assert strat.state == StrategyState.LONG_PE


# ==============================================================================
# 4. Atomic Two-Phase Reversal Logic
# ==============================================================================

def test_reversal_ce_to_pe_atomic_two_phase_sequencing(standard_strategy: NiftyOrbStrategy):
    """
    CRITICAL: Verify reversal sequencing:
    1. Holding CE and downside breakout occurs -> Emit CLOSE_CE only.
    2. Transition to REVERSING_CE_TO_PE.
    3. DO NOT emit ENTER_LONG_PE until CE exit fill is confirmed.
    4. Upon Fill confirmation, emit ENTER_LONG_PE and transition to LONG_PE.
    """
    strat = standard_strategy

    # Setup range: High=24500, Low=24400
    strat.on_candle(make_candle("09:16:00", "24450", "24500", "24400", "24450"))
    strat.on_candle(make_candle("09:45:00", "24450", "24480", "24420", "24460"))

    # Initial entry: Upside breakout to 24550 -> Long CE
    strat.on_candle(make_candle("09:46:00", "24460", "24560", "24450", "24550"))
    assert strat.state == StrategyState.LONG_CE
    ce_contract = strat.execution.active_contract

    # Reversal bar: Price plunges to 24350 (breaks below OR_LOW 24400)
    reversal_bar = make_candle("10:15:00", "24500", "24510", "24340", "24350")
    reversal_signals = strat.on_candle(reversal_bar)

    # 1. MUST only emit CLOSE_CE, NOT ENTER_LONG_PE
    assert len(reversal_signals) == 1
    assert reversal_signals[0].signal_type == SignalType.CLOSE_CE
    assert reversal_signals[0].target_contract == ce_contract
    assert strat.state == StrategyState.REVERSING_CE_TO_PE

    # 2. Incoming bars while awaiting fill must produce ZERO signals
    intermediate_bar = make_candle("10:16:00", "24350", "24360", "24330", "24340")
    assert strat.on_candle(intermediate_bar) == []
    assert strat.state == StrategyState.REVERSING_CE_TO_PE

    # 3. Execution layer confirms CE exit fill
    fill_exit = Fill(
        client_order_id="ORD_EXIT_CE",
        symbol=ce_contract.symbol,
        side=OrderSide.SELL,
        fill_price=Decimal("120.00"),
        fill_qty=25,
        timestamp=reversal_bar.timestamp,
    )
    replacement_signals = strat.on_order_fill(fill_exit)

    # 4. NOW replacement ENTER_LONG_PE is emitted and state moves to LONG_PE
    assert len(replacement_signals) == 1
    pe_sig = replacement_signals[0]
    assert pe_sig.signal_type == SignalType.ENTER_LONG_PE
    assert pe_sig.target_contract is not None
    assert pe_sig.target_contract.option_type == OptionType.PE
    assert pe_sig.target_contract.strike == Decimal("24350")
    assert strat.state == StrategyState.LONG_PE
    assert strat.execution.active_contract == pe_sig.target_contract


def test_reversal_pe_to_ce_atomic_two_phase_sequencing(standard_strategy: NiftyOrbStrategy):
    """Verify symmetric reversal from Long PE to Long CE."""
    strat = standard_strategy

    # Setup range: High=24500, Low=24400
    strat.on_candle(make_candle("09:16:00", "24450", "24500", "24400", "24450"))
    strat.on_candle(make_candle("09:45:00", "24450", "24480", "24420", "24460"))

    # Initial entry: Downside breakout to 24350 -> Long PE
    strat.on_candle(make_candle("09:46:00", "24450", "24460", "24340", "24350"))
    assert strat.state == StrategyState.LONG_PE
    pe_contract = strat.execution.active_contract

    # Reversal bar: Price surges to 24550 (breaks above OR_HIGH 24500)
    reversal_bar = make_candle("11:00:00", "24400", "24560", "24390", "24550")
    reversal_signals = strat.on_candle(reversal_bar)

    assert len(reversal_signals) == 1
    assert reversal_signals[0].signal_type == SignalType.CLOSE_PE
    assert strat.state == StrategyState.REVERSING_PE_TO_CE

    # Confirm PE exit fill
    fill_exit = Fill(
        client_order_id="ORD_EXIT_PE",
        symbol=pe_contract.symbol,
        side=OrderSide.SELL,
        fill_price=Decimal("95.00"),
        fill_qty=25,
        timestamp=reversal_bar.timestamp,
    )
    replacement_signals = strat.on_order_fill(fill_exit)

    assert len(replacement_signals) == 1
    ce_sig = replacement_signals[0]
    assert ce_sig.signal_type == SignalType.ENTER_LONG_CE
    assert ce_sig.target_contract.option_type == OptionType.CE
    assert ce_sig.target_contract.strike == Decimal("24550")
    assert strat.state == StrategyState.LONG_CE


# ==============================================================================
# 5. Deduplication & Idempotency
# ==============================================================================

def test_deduplication_of_candles(standard_strategy: NiftyOrbStrategy):
    """Verify that replayed, identical, or stale candles produce zero duplicate signals."""
    strat = standard_strategy

    strat.on_candle(make_candle("09:16:00", "24450", "24500", "24400", "24450"))
    strat.on_candle(make_candle("09:45:00", "24450", "24480", "24420", "24460"))

    # First breakout candle generates signal
    c_break = make_candle("09:46:00", "24460", "24560", "24450", "24550")
    sig1 = strat.on_candle(c_break)
    assert len(sig1) == 1

    # Ingest identical candle again (duplicate/replay)
    sig_dup = strat.on_candle(c_break)
    assert len(sig_dup) == 0

    # Ingest candle with older timestamp
    c_older = make_candle("09:40:00", "24460", "24480", "24450", "24470")
    sig_old = strat.on_candle(c_older)
    assert len(sig_old) == 0

    # Next continuous bar still above range while holding CE produces NO new entry
    c_next = make_candle("09:47:00", "24550", "24580", "24540", "24570")
    sig_next = strat.on_candle(c_next)
    assert len(sig_next) == 0


# ==============================================================================
# 6. Risk Rules: Hard Daily Loss Threshold (-₹3,000)
# ==============================================================================

def test_daily_loss_limit_kill_switch_flatten_and_lockout(standard_strategy: NiftyOrbStrategy):
    """Verify that cumulative loss of -₹3,000 triggers kill switch, flattens position, and locks session."""
    strat = standard_strategy

    # Setup and enter Long CE
    strat.on_candle(make_candle("09:16:00", "24450", "24500", "24400", "24450"))
    strat.on_candle(make_candle("09:45:00", "24450", "24480", "24420", "24460"))
    strat.on_candle(make_candle("09:46:00", "24460", "24560", "24450", "24550"))
    assert strat.state == StrategyState.LONG_CE

    # MTM drops to -₹3,000
    risk_signals = strat.on_mtm_update(
        realized_pnl=Decimal("0.00"),
        unrealized_pnl=Decimal("-3050.00"),
        timestamp=datetime.now(timezone.utc),
    )

    assert len(risk_signals) == 1
    assert risk_signals[0].signal_type == SignalType.CLOSE_CE
    assert "Daily MTM loss limit breached" in risk_signals[0].reason
    assert strat.state == StrategyState.LOCKED_OUT_FOR_DAY
    assert not strat.daily_risk.can_trade

    # Further candles or breakouts are strictly rejected
    c_after = make_candle("10:00:00", "24300", "24310", "24200", "24250")
    assert strat.on_candle(c_after) == []
    assert strat.state == StrategyState.LOCKED_OUT_FOR_DAY


# ==============================================================================
# 7. Profit-Protection & Trailing Mechanics
# ==============================================================================

def test_profit_protection_lock_and_pullback_exit(standard_strategy: NiftyOrbStrategy):
    """Verify profit-protection trigger (+₹3,000) locks floor (+₹500), exiting on pullback."""
    strat = standard_strategy

    strat.on_candle(make_candle("09:16:00", "24450", "24500", "24400", "24450"))
    strat.on_candle(make_candle("09:45:00", "24450", "24480", "24420", "24460"))
    strat.on_candle(make_candle("09:46:00", "24460", "24560", "24450", "24550"))
    assert strat.state == StrategyState.LONG_CE

    # 1. Profit crosses +₹3,000 threshold -> Floor locked at ₹500
    assert strat.on_mtm_update(Decimal("0.00"), Decimal("3200.00")) == []
    assert strat.profit_protection.is_activated
    assert strat.profit_protection.current_floor == Decimal("500.00")
    assert strat.state == StrategyState.LONG_CE

    # 2. Profit retreats to ₹450 (below locked ₹500 floor) -> Exit triggered
    exit_signals = strat.on_mtm_update(Decimal("0.00"), Decimal("450.00"))
    assert len(exit_signals) == 1
    assert exit_signals[0].signal_type == SignalType.CLOSE_CE
    assert "Profit protection floor breached" in exit_signals[0].reason
    assert strat.state == StrategyState.PROFIT_LOCKED


def test_profit_protection_step_trailing(standard_strategy: NiftyOrbStrategy):
    """Verify step-based trailing: MTM crosses +₹4,000 (1 step), advancing floor to ₹1,000."""
    strat = standard_strategy

    strat.on_candle(make_candle("09:16:00", "24450", "24500", "24400", "24450"))
    strat.on_candle(make_candle("09:45:00", "24450", "24480", "24420", "24460"))
    strat.on_candle(make_candle("09:46:00", "24460", "24560", "24450", "24550"))

    # Peak MTM reaches +₹4,200 (+₹1,200 beyond ₹3,000 trigger -> 1 step of ₹1,000 completed)
    strat.on_mtm_update(Decimal("0.00"), Decimal("4200.00"))
    assert strat.profit_protection.is_activated
    assert strat.profit_protection.current_floor == Decimal("1000.00")  # 500 min lock + 500 step

    # Pullback to ₹950 (<= ₹1,000 floor)
    exit_signals = strat.on_mtm_update(Decimal("0.00"), Decimal("950.00"))
    assert len(exit_signals) == 1
    assert exit_signals[0].signal_type == SignalType.CLOSE_CE
    assert strat.state == StrategyState.PROFIT_LOCKED


# ==============================================================================
# 8. Mandatory Square-off at 15:10 IST
# ==============================================================================

def test_mandatory_eod_square_off_at_1510(standard_strategy: NiftyOrbStrategy):
    """Verify mandatory intraday square-off occurs unconditionally at 15:10 IST."""
    strat = standard_strategy

    strat.on_candle(make_candle("09:16:00", "24450", "24500", "24400", "24450"))
    strat.on_candle(make_candle("09:45:00", "24450", "24480", "24420", "24460"))
    strat.on_candle(make_candle("09:46:00", "24460", "24560", "24450", "24550"))
    assert strat.state == StrategyState.LONG_CE

    # 15:10 IST bar arrives
    c_1510 = make_candle("15:10:00", "24520", "24530", "24510", "24520")
    signals = strat.on_candle(c_1510)

    assert len(signals) == 1
    assert signals[0].signal_type == SignalType.CLOSE_CE
    assert "Mandatory intraday EOD square-off at 15:10:00 IST" in signals[0].reason
    assert strat.state == StrategyState.SQUARED_OFF

    # Post-15:10 bar produces nothing
    c_1511 = make_candle("15:11:00", "24520", "24530", "24500", "24510")
    assert strat.on_candle(c_1511) == []


# ==============================================================================
# 9. Intrabar Tick Breakout Confirmation Mode
# ==============================================================================

def test_intrabar_tick_breakout_mode(mock_contract_resolver: ContractResolver):
    """Verify that intrabar tick breach immediately generates breakout signals."""
    strat_cfg = StrategyConfig(
        underlying_symbol="NSE:NIFTY50-INDEX",
        breakout_confirmation_mode=BreakoutConfirmationMode.INTRABAR_TICK,
        range_boundary_mode=RangeBoundaryMode.UP_TO_0944_CLOSE,
        expiry_day_selection=ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE,
    )
    risk_cfg = RiskConfig(profit_trailing_mode=ProfitTrailingMode.LOCK_AND_TRAIL)
    strat = NiftyOrbStrategy(strat_cfg, risk_cfg, mock_contract_resolver)
    strat.on_session_start("SESS_TICK", date(2026, 11, 6))

    # Build range with ticks
    strat.on_tick(make_tick("09:16:05", "24450"))
    strat.on_tick(make_tick("09:20:00", "24500"))  # High
    strat.on_tick(make_tick("09:30:00", "24400"))  # Low
    assert strat.state == StrategyState.CALCULATING_RANGE

    # Finalize at 09:45:00
    strat.on_tick(make_tick("09:45:00", "24450"))
    assert strat.state == StrategyState.RANGE_ESTABLISHED
    assert strat.range_tracker.high == Decimal("24500")

    # Tick breach above 24500
    breakout_tick = make_tick("09:45:15", "24510")
    signals = strat.on_tick(breakout_tick)

    assert len(signals) == 1
    assert signals[0].signal_type == SignalType.ENTER_LONG_CE
    assert signals[0].underlying_price == Decimal("24510")
    assert strat.state == StrategyState.LONG_CE


# ==============================================================================
# 10. Deterministic Replay Parity
# ==============================================================================

def test_deterministic_replay_produces_identical_signals(mock_contract_resolver: ContractResolver):
    """Verify that replaying identical candle series on separate instances produces identical signals."""
    candles = [
        make_candle("09:16:00", "24450", "24500", "24400", "24450"),
        make_candle("09:45:00", "24450", "24480", "24420", "24460"),
        make_candle("09:46:00", "24460", "24560", "24450", "24550"),  # Long CE
        make_candle("10:00:00", "24550", "24580", "24350", "24370"),  # Reversal to PE
    ]

    def run_simulation() -> list:
        strat_cfg = StrategyConfig(
            breakout_confirmation_mode=BreakoutConfirmationMode.CANDLE_CLOSE,
            range_boundary_mode=RangeBoundaryMode.UP_TO_0944_CLOSE,
            expiry_day_selection=ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE,
        )
        risk_cfg = RiskConfig(profit_trailing_mode=ProfitTrailingMode.LOCK_AND_TRAIL)
        inst = NiftyOrbStrategy(strat_cfg, risk_cfg, mock_contract_resolver)
        inst.on_session_start("REPLAY_SESS", date(2026, 11, 6))

        all_signals = []
        for c in candles:
            sigs = inst.on_candle(c)
            all_signals.extend(sigs)
            if inst.state == StrategyState.REVERSING_CE_TO_PE:
                # Simulate instantaneous exit fill
                fill = Fill(
                    client_order_id="EXIT_ORD",
                    symbol=inst.execution.active_contract.symbol if inst.execution.active_contract else "NSE:DUMMY",
                    side=OrderSide.SELL,
                    fill_price=Decimal("100.00"),
                    fill_qty=25,
                    timestamp=c.timestamp,
                )
                repl_sigs = inst.on_order_fill(fill)
                all_signals.extend(repl_sigs)
        return [(s.signal_type, s.underlying_price, s.sequence_number) for s in all_signals]

    run1 = run_simulation()
    run2 = run_simulation()

    assert len(run1) == 3
    assert run1 == run2
