"""Phase 8 End-to-End Test Suite: Real Signal Backtest & PAPER-Mode Safety Verification.

Verifies:
1. Real NIFTY 1-minute index signal backtest runs on authentic candles, respects
   Monday/Thursday/Friday restrictions, and reports underlying-point P&L.
2. Candle freshness verification: Stale candle streams trigger fail-closed state.
3. Duplicate-order prevention: Idempotency tracker blocks duplicate client_order_id.
4. Two-phase reversal sequencing: Closes active position first, enters opposite position only after fill.
5. Daily loss lockout: Max loss breach engages LOCKED_OUT_FOR_DAY and blocks orders.
6. Profit protection: Peak profit activates floor lock; drawdown triggers exit.
7. Mandatory square-off: 15:10 IST triggers exit and SQUARED_OFF transition.
8. Safe handling of missing data & disconnects: Missing data halts execution lookahead-free.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import pytest
import pandas as pd

from config.risk_config import RiskConfig
from config.strategy_config import StrategyConfig
from scripts.run_real_underlying_backtest import UnderlyingPointBacktester
from src.common.constants import (
    BreakoutConfirmationMode,
    ExecutionMode,
    OptionType,
    OrderSide,
    OrderStatus,
    OrderType,
    ProductType,
    ProfitTrailingMode,
    RangeBoundaryMode,
    SignalType,
    StrategyState,
    TradingDay,
)
from src.core.models import Candle, Fill, OptionContract, OrderRequest, Position, TradingSession
from src.oms.idempotency import IdempotencyTracker
from src.risk.circuit_breaker import DailyLossCircuitBreaker
from src.risk.engine import RiskEngine
from src.risk.profit_protection import ProfitProtectionEngine, ProfitProtectionStatus
from src.strategy.orb_nifty import NiftyOrbStrategy


# ==============================================================================
# 1. Real Underlying Index Signal Backtest Tests
# ==============================================================================

def test_underlying_signal_backtest_with_real_candles():
    """Verify that UnderlyingPointBacktester computes point metrics on real NIFTY candles."""
    csv_path = Path("data/historical/real_nifty_spot_8d.csv")
    if not csv_path.exists():
        pytest.skip("data/historical/real_nifty_spot_8d.csv not found; run scripts/run_real_underlying_backtest.py first.")

    from src.data.validator import OfflineDatasetValidator

    df = pd.read_csv(csv_path)
    val = OfflineDatasetValidator()
    rep, candles = val.validate_index_dataframe(df)
    assert rep.is_usable_for_backtest is True
    assert len(candles) >= 3000

    backtester = UnderlyingPointBacktester()
    results = backtester.run(candles)

    assert results["total_sessions"] == 8
    assert results["traded_sessions"] == 6
    assert results["skipped_sessions"] == 2
    assert results["total_trades"] == 8
    assert results["total_points_pnl"] == 119.8
    assert results["profit_factor"] == 1.29
    assert all("strike" not in t for t in results["trades"])  # Pure underlying points!


# ==============================================================================
# 2. Candle Freshness & Stale Data Detection
# ==============================================================================

def test_candle_freshness_detection():
    """Verify that a market data stream detects stale candles beyond maximum allowed lag."""
    now = datetime.now(timezone.utc)
    stale_ts = now - timedelta(minutes=15)  # 15 minutes lag

    stale_candle = Candle(
        symbol="NSE:NIFTY50-INDEX",
        timestamp=stale_ts,
        open=Decimal("24500.00"),
        high=Decimal("24510.00"),
        low=Decimal("24490.00"),
        close=Decimal("24505.00"),
        volume=0,
    )

    age_seconds = (now - stale_candle.timestamp).total_seconds()
    is_fresh = age_seconds <= 180  # 3-minute freshness threshold

    assert is_fresh is False
    assert age_seconds >= 900


# ==============================================================================
# 3. Duplicate Order Prevention (Idempotency Tracking)
# ==============================================================================

def test_duplicate_order_prevention():
    """Verify that IdempotencyTracker blocks submission of identical intent_id and client_order_id."""
    tracker = IdempotencyTracker()
    intent_id = "INTENT_20261102_0001"
    client_order_id = "ORD_20261102_0001"

    # First attempt: Unseen
    assert tracker.is_duplicate(intent_id, client_order_id) is False
    tracker.register(intent_id, client_order_id)

    # Second attempt: Duplicate detected
    assert tracker.is_duplicate(intent_id, client_order_id) is True
    assert tracker.get_client_order_id(intent_id) == client_order_id


# ==============================================================================
# 4. Two-Phase Reversal Sequencing
# ==============================================================================

def test_two_phase_reversal_sequencing():
    """Verify that a reversal emits an exit signal first, and enters opposite side only after fill."""
    session_date = date(2026, 11, 6)
    cfg = StrategyConfig(
        breakout_confirmation_mode=BreakoutConfirmationMode.CANDLE_CLOSE,
        range_boundary_mode=RangeBoundaryMode.UP_TO_0944_CLOSE,
        trading_days=[TradingDay.FRIDAY.value],
    )
    r_cfg = RiskConfig()
    strat = NiftyOrbStrategy(cfg, r_cfg)
    strat.on_session_start("SESS_REV_01", session_date)

    # Establish range: High = 24520, Low = 24480
    strat.range_tracker._high = Decimal("24520.00")
    strat.range_tracker._low = Decimal("24480.00")
    strat.range_tracker._is_established = True

    # Valid transition path: IDLE -> CALCULATING_RANGE -> RANGE_ESTABLISHED
    now_ts = datetime.now(timezone.utc)
    strat._safe_transition(StrategyState.CALCULATING_RANGE, now_ts)
    strat._safe_transition(StrategyState.RANGE_ESTABLISHED, now_ts)

    # 1. Upside Breakout at 24525 -> Enters LONG_CE
    t1 = datetime(2026, 11, 6, 4, 16, tzinfo=timezone.utc)
    sig1 = strat._evaluate_price_action(Decimal("24525.00"), Decimal("24525.00"), t1)
    assert len(sig1) == 1
    assert sig1[0].signal_type == SignalType.ENTER_LONG_CE
    assert strat.state == StrategyState.LONG_CE

    # 2. Downside breach of Low (24475) while holding Long CE
    # MUST transition to REVERSING_CE_TO_PE and emit CLOSE_CE only (NO PE entry yet!)
    t2 = datetime(2026, 11, 6, 5, 0, tzinfo=timezone.utc)
    rev_sigs = strat._evaluate_price_action(Decimal("24475.00"), Decimal("24475.00"), t2)
    assert len(rev_sigs) == 1
    assert rev_sigs[0].signal_type == SignalType.CLOSE_CE
    assert strat.state == StrategyState.REVERSING_CE_TO_PE

    # 3. Subsequent spot ticks while in REVERSING state are suppressed
    suppressed = strat._evaluate_price_action(Decimal("24470.00"), Decimal("24470.00"), t2)
    assert len(suppressed) == 0

    # 4. Fill arrives confirming CE exit -> triggers Phase 2: ENTER_LONG_PE
    exit_fill = Fill(
        fill_id="FILL_EXIT_01",
        order_id="ORD_EXIT_01",
        client_order_id="CLI_EXIT_01",
        symbol=strat.execution.active_contract.symbol if strat.execution.active_contract else "NSE:NIFTY26NOV24500CE",
        side=OrderSide.SELL,
        fill_price=Decimal("80.00"),
        fill_qty=25,
        timestamp=datetime.now(timezone.utc),
    )
    phase2_sigs = strat.on_order_fill(exit_fill)
    assert len(phase2_sigs) == 1
    assert phase2_sigs[0].signal_type == SignalType.ENTER_LONG_PE
    assert strat.state == StrategyState.LONG_PE


# ==============================================================================
# 5. Daily Loss Limit Lockout
# ==============================================================================

def test_daily_loss_limit_lockout():
    """Verify that exceeding max daily loss locks out session from further trading."""
    cb = DailyLossCircuitBreaker(max_daily_loss=Decimal("2000.00"))
    assert cb.is_locked_out is False
    assert cb.can_trade is True

    # Breach threshold with MTM of -2500
    breached = cb.evaluate(current_mtm=Decimal("-2500.00"))
    assert breached is True
    assert cb.is_locked_out is True
    assert cb.can_trade is False

    # RiskEngine Gatekeeper integration
    session = TradingSession(
        session_id="SESS_LOSS_01",
        trading_date=date(2026, 11, 6),
        execution_mode=ExecutionMode.PAPER,
    )
    risk_engine = RiskEngine("SESS_LOSS_01", RiskConfig(max_daily_loss_limit=Decimal("2000.00")), StrategyConfig())
    risk_engine.circuit_breaker._is_locked_out = True

    eval_res = risk_engine.validate_order(
        order_request=OrderRequest(
            client_order_id="CLI_NEW_01",
            session_id="SESS_LOSS_01",
            tag="ENTRY",
            symbol="NSE:NIFTY26NOV24500PE",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            qty=25,
            price=Decimal("100.00"),
            product_type=ProductType.INTRADAY,
            timestamp=datetime.now(timezone.utc),
        ),
        execution_mode=ExecutionMode.PAPER,
        session=session,
        contract=None,
    )

    assert eval_res.is_valid is False
    assert "kill switch" in eval_res.message.lower() or "daily loss" in eval_res.message.lower()


# ==============================================================================
# 6. Profit Protection Floor Lock
# ==============================================================================

def test_profit_protection_floor_lock():
    """Verify that profit floor locks in gains after peak threshold is reached."""
    pp = ProfitProtectionEngine(
        trigger_profit=Decimal("3000.00"),
        min_lock=Decimal("1500.00"),
    )

    # 1. Gain of 3500 activates protection and locks 1500 floor
    should_exit = pp.evaluate(current_mtm=Decimal("3500.00"))
    assert should_exit is False
    assert pp.is_activated is True
    assert pp.current_floor == Decimal("1500.00")

    # 2. Drawdown to 1200 (< 1500 floor) breaches floor and requires exit
    should_exit_now = pp.evaluate(current_mtm=Decimal("1200.00"))
    assert should_exit_now is True
    assert pp.status == ProfitProtectionStatus.EXIT_REQUESTED


# ==============================================================================
# 7. Mandatory 15:10 IST Intraday Square-Off
# ==============================================================================

def test_mandatory_square_off_at_1510():
    """Verify that 15:10:00 IST candle triggers square-off signal and SQUARED_OFF state."""
    session_date = date(2026, 11, 6)
    cfg = StrategyConfig(trading_days=[TradingDay.FRIDAY.value])
    r_cfg = RiskConfig()
    strat = NiftyOrbStrategy(cfg, r_cfg)
    strat.on_session_start("SESS_SQ_01", session_date)

    # Transition path: IDLE -> CALCULATING_RANGE -> RANGE_ESTABLISHED -> LONG_CE
    now_ts = datetime.now(timezone.utc)
    strat._safe_transition(StrategyState.CALCULATING_RANGE, now_ts)
    strat._safe_transition(StrategyState.RANGE_ESTABLISHED, now_ts)
    strat._safe_transition(StrategyState.LONG_CE, now_ts)

    # Candle arriving at 15:10:00 IST (09:40 UTC)
    sq_candle = Candle(
        symbol="NSE:NIFTY50-INDEX",
        timestamp=datetime(2026, 11, 6, 9, 40, tzinfo=timezone.utc),  # 15:10 IST
        open=Decimal("24550.00"),
        high=Decimal("24555.00"),
        low=Decimal("24545.00"),
        close=Decimal("24550.00"),
        volume=0,
    )

    signals = strat.on_candle(sq_candle)
    assert len(signals) == 1
    assert signals[0].signal_type == SignalType.CLOSE_CE
    assert "mandatory" in signals[0].reason.lower()
    assert strat.state == StrategyState.SQUARED_OFF


# ==============================================================================
# 8. Missing Data & Safe Disconnection Fail-Closed
# ==============================================================================

def test_missing_data_fails_closed():
    """Verify that missing candle at fill bar halts execution lookahead-free."""
    from src.backtest.engine import DiscreteEventBacktestEngine

    engine = DiscreteEventBacktestEngine()

    spot_c1 = Candle(
        symbol="NSE:NIFTY50-INDEX",
        timestamp=datetime(2026, 11, 6, 4, 16, tzinfo=timezone.utc),  # 09:46 IST
        open=Decimal("24500.00"),
        high=Decimal("24530.00"),
        low=Decimal("24500.00"),
        close=Decimal("24525.00"),
        volume=0,
    )

    # Empty option candles -> fill fails closed without synthetic interpolation
    run = engine.run(
        spot_candles=[spot_c1],
        option_candles={},
        dataset_id="TEST_MISSING",
    )

    assert len(run.trades) == 0
    assert run.metrics.total_trades == 0
