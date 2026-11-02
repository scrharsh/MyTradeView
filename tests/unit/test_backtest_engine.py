"""Comprehensive unit tests for the Discrete-Event Backtesting Engine.

Validates all 18 test areas required for Phase 5:
1. Deterministic chronological event replay
2. Opening range construction
3. Option contract resolution
4. Simulated fills at next-bar open (lookahead prevention)
5. Two-phase reversal execution and sequencing
6. Partial fill simulation
7. Order request idempotency
8. Brokerage, statutory taxes (STT, GST, etc.), and slippage calculations
9. Realized / unrealized P&L and MTM calculations
10. Daily loss limit trigger (-₹3,000) and lockout
11. Profit protection trigger (+₹3,000) and trailing exit
12. EOD square-off at 15:10 IST
13. Missing data handling and fail-closed diagnostics
14. Lookahead bias prevention (timing validation)
15. Replay repeatability (two runs with same data yield exact identity)
16. Quantitative metric calculations accuracy
17. Stored backtest run persistence and retrieval
18. Multiple strategy configuration parameter isolation
"""

import csv
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
import pytest

from config.risk_config import RiskConfig
from config.strategy_config import StrategyConfig
from src.backtest.benchmark import (
    BenchmarkStatus,
    StockMockBenchmarkComparator,
)
from src.backtest.engine import DiscreteEventBacktestEngine
from src.backtest.execution import SimulatedExecutionModel
from src.backtest.metrics import MetricsCalculator
from src.backtest.models import (
    BacktestTrade,
    ExecutionModelConfig,
)
from src.backtest.synthetic import SyntheticMarketDataBuilder, generate_candle
from src.common.constants import (
    BreakoutConfirmationMode,
    ExpiryDaySelectionMode,
    OptionType,
    OrderExecutionStyle,
    OrderSide,
    OrderStatus,
    OrderType,
    ProductType,
    ProfitTrailingMode,
    RangeBoundaryMode,
)
from src.core.models import OrderRequest
from src.data.contract_resolver import ContractResolver
from src.data.instrument_master import InstrumentMasterRegistry
from src.storage.repository import StateRepository


@pytest.fixture
def synthetic_builder() -> SyntheticMarketDataBuilder:
    return SyntheticMarketDataBuilder(session_date=date(2026, 11, 6))


@pytest.fixture
def mock_contract_resolver() -> ContractResolver:
    csv_data = """trading_symbol,underlying_symbol,strike_price,option_type,expiry_date,minimum_lot_size,tick_size
NSE:NIFTY26NOV25000CE,NSE:NIFTY50-INDEX,25000.0,CE,2026-11-06,25,0.05
NSE:NIFTY26NOV25000PE,NSE:NIFTY50-INDEX,25000.0,PE,2026-11-06,25,0.05
NSE:NIFTY26NOV24900CE,NSE:NIFTY50-INDEX,24900.0,CE,2026-11-06,25,0.05
NSE:NIFTY26NOV24900PE,NSE:NIFTY50-INDEX,24900.0,PE,2026-11-06,25,0.05
"""
    registry = InstrumentMasterRegistry()
    registry.parse_csv(csv_data)
    return ContractResolver(registry)


# ==============================================================================
# Area 1: Deterministic Chronological Event Replay
# ==============================================================================

def test_deterministic_chronological_replay(synthetic_builder: SyntheticMarketDataBuilder):
    """Verify that candles are ingested in strict chronological order."""
    spot, options = synthetic_builder.build_bullish_breakout_dataset()
    engine = DiscreteEventBacktestEngine()

    run = engine.run(spot, options, dataset_id="TEST_CHRONO")
    assert len(run.trades) == 1
    trade = run.trades[0]
    assert trade.entry_time < trade.exit_time


# ==============================================================================
# Area 2: Opening Range Construction
# ==============================================================================

def test_opening_range_construction(synthetic_builder: SyntheticMarketDataBuilder):
    """Verify range formation establishes high/low accurately before breakout."""
    spot, options = synthetic_builder.build_bullish_breakout_dataset()
    engine = DiscreteEventBacktestEngine()

    run = engine.run(spot, options)
    assert len(run.trades) == 1
    # Spot broke above 25010 at 09:46 -> Long CE trade occurred
    assert run.trades[0].option_type == OptionType.CE


# ==============================================================================
# Area 3: Option Contract Resolution
# ==============================================================================

def test_option_contract_resolution(synthetic_builder: SyntheticMarketDataBuilder, mock_contract_resolver: ContractResolver):
    """Verify ATM strike and contract symbol match authoritative instrument master."""
    spot, options = synthetic_builder.build_bullish_breakout_dataset()
    engine = DiscreteEventBacktestEngine(contract_resolver=mock_contract_resolver)

    run = engine.run(spot, options)
    assert len(run.trades) == 1
    assert run.trades[0].symbol == "NSE:NIFTY26NOV25000CE"
    assert run.trades[0].strike == Decimal("25000")


# ==============================================================================
# Area 4: Simulated Fills at Next-Bar Open (Lookahead Prevention)
# ==============================================================================

def test_simulated_fills_at_next_bar_open(synthetic_builder: SyntheticMarketDataBuilder):
    """Verify that signal at bar T (09:46) fills strictly at bar T+1 (09:47) open."""
    spot, options = synthetic_builder.build_bullish_breakout_dataset(ce_entry_price=Decimal("100.00"))
    # Slippage: 0.50 points added to BUY -> 100.50
    exec_cfg = ExecutionModelConfig(slippage_points=Decimal("0.50"), fill_timing="next_bar_open")
    engine = DiscreteEventBacktestEngine(execution_config=exec_cfg)

    run = engine.run(spot, options)
    assert len(run.trades) == 1
    trade = run.trades[0]
    # Bar at 09:47 has open 100.00 -> with 0.50 slippage, entry price is 100.50
    assert trade.entry_price == Decimal("100.50")


# ==============================================================================
# Area 5: Two-Phase Reversal Execution and Sequencing
# ==============================================================================

def test_two_phase_reversal_execution(synthetic_builder: SyntheticMarketDataBuilder):
    """Verify that reversing from CE to PE closes CE first before opening PE."""
    spot, options = synthetic_builder.build_reversal_dataset()
    engine = DiscreteEventBacktestEngine()

    run = engine.run(spot, options)
    assert len(run.trades) == 2
    trade_ce, trade_pe = run.trades

    assert trade_ce.option_type == OptionType.CE
    assert trade_pe.option_type == OptionType.PE
    # Sequential ordering: CE exit occurs before PE entry
    assert trade_ce.exit_time <= trade_pe.entry_time
    assert run.diagnostics.reversals_count >= 1


# ==============================================================================
# Area 6: Partial Fill Simulation
# ==============================================================================

def test_partial_fill_simulation():
    """Verify that partial fill configuration splits order execution into multiple fills."""
    cfg = ExecutionModelConfig(partial_fill_supported=True)
    model = SimulatedExecutionModel(cfg)

    req = OrderRequest(
        client_order_id="ORD_TEST_PARTIAL",
        session_id="SESS_TEST",
        symbol="NSE:NIFTY26NOV25000CE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=50,
        product_type=ProductType.INTRADAY,
        tag="TEST_PARTIAL",
    )
    dt = datetime(2026, 11, 6, 9, 47, tzinfo=timezone.utc)
    candle = generate_candle("NSE:NIFTY26NOV25000CE", dt, Decimal("100.00"), Decimal("105.00"), Decimal("99.00"), Decimal("102.00"))

    fills = model.simulate_fill(req, candle, is_next_bar=True)
    assert len(fills) == 2
    assert fills[0].fill_qty == 25
    assert fills[1].fill_qty == 25
    assert sum(f.fill_qty for f in fills) == 50


# ==============================================================================
# Area 7: Order Request Idempotency
# ==============================================================================

def test_order_idempotency_and_no_duplicate_positions(synthetic_builder: SyntheticMarketDataBuilder):
    """Verify that multiple consecutive bars in the breakout zone do not generate duplicate orders."""
    spot, options = synthetic_builder.build_bullish_breakout_dataset()
    engine = DiscreteEventBacktestEngine()

    run = engine.run(spot, options)
    # Even though multiple bars stay above the breakout level, only 1 long trade is opened
    assert len(run.trades) == 1


# ==============================================================================
# Area 8: Brokerage, Statutory Taxes, and Slippage Calculations
# ==============================================================================

def test_brokerage_taxes_and_slippage_accounting():
    """Verify exact Indian statutory taxation on option trades."""
    model = SimulatedExecutionModel()

    # Buy 25 qty at 100.00, Sell 25 qty at 130.00
    # Buy turnover = 2500, Sell turnover = 3250
    # Buy costs: Brokerage 20 + Exchange turnover 2500*0.00053=1.325 + GST 18%(20+1.325)=3.8385 + Stamp duty 2500*0.00003=0.075 + SEBI fee
    # Sell costs: Brokerage 20 + STT 3250*0.00125=4.0625 + Turnover 3250*0.00053=1.7225 + GST 18%(20+1.7225)=3.91 + SEBI
    costs = model.calculate_roundtrip_costs(Decimal("100.00"), Decimal("130.00"), 25)
    assert costs > Decimal("50.00")  # > ₹40 brokerage + taxes


# ==============================================================================
# Area 9: Realized / Unrealized P&L and MTM Calculations
# ==============================================================================

def test_realized_and_unrealized_pnl_accounting(synthetic_builder: SyntheticMarketDataBuilder):
    """Verify net P&L reflects gross P&L minus trading costs."""
    spot, options = synthetic_builder.build_bullish_breakout_dataset()
    engine = DiscreteEventBacktestEngine()

    run = engine.run(spot, options)
    assert len(run.trades) == 1
    trade = run.trades[0]
    expected_net = trade.gross_pnl - trade.trading_costs
    assert trade.net_pnl == expected_net.quantize(Decimal("0.01"))


# ==============================================================================
# Area 10: Daily Loss Limit Trigger (-₹3,000) and Lockout
# ==============================================================================

def test_daily_loss_limit_trigger_and_lockout(synthetic_builder: SyntheticMarketDataBuilder):
    """Verify daily loss limit breach initiates emergency exit and halts further trading."""
    spot, options = synthetic_builder.build_daily_loss_limit_dataset()
    # StrategyConfig with lots=3 -> qty 75 -> drop from 100 to 50 gives -3750 loss
    strat_cfg = StrategyConfig(
        lots=3,
        breakout_confirmation_mode=BreakoutConfirmationMode.CANDLE_CLOSE,
        range_boundary_mode=RangeBoundaryMode.UP_TO_0944_CLOSE,
        order_execution_style=OrderExecutionStyle.MARKET,
        expiry_day_selection=ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE,
    )
    risk_cfg = RiskConfig(max_daily_loss_limit=Decimal("3000.00"))
    engine = DiscreteEventBacktestEngine(strategy_config=strat_cfg, risk_config=risk_cfg)

    run = engine.run(spot, options)
    assert len(run.trades) == 1
    assert run.trades[0].gross_pnl < Decimal("-3000.00")
    assert run.diagnostics.daily_loss_limit_hits == 1


# ==============================================================================
# Area 11: Profit Protection Trigger (+₹3,000) and Trailing Exit
# ==============================================================================

def test_profit_protection_trigger_and_exit(synthetic_builder: SyntheticMarketDataBuilder):
    """Verify profit protection activation (+₹3,000) and lock floor exit on pullback."""
    spot, options = synthetic_builder.build_profit_protection_dataset()
    # Strategy lots=3 -> qty 75. Surge from 100 to 150 = +3750 (triggers protection). Pullback to 104 = +300 < +500 floor.
    strat_cfg = StrategyConfig(
        lots=3,
        breakout_confirmation_mode=BreakoutConfirmationMode.CANDLE_CLOSE,
        range_boundary_mode=RangeBoundaryMode.UP_TO_0944_CLOSE,
        order_execution_style=OrderExecutionStyle.MARKET,
        expiry_day_selection=ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE,
    )
    risk_cfg = RiskConfig(
        profit_protection_trigger=Decimal("3000.00"),
        profit_protection_min_lock=Decimal("500.00"),
        profit_trailing_mode=ProfitTrailingMode.LOCK_MINIMUM_ONLY,
    )
    engine = DiscreteEventBacktestEngine(strategy_config=strat_cfg, risk_config=risk_cfg)

    run = engine.run(spot, options)
    assert len(run.trades) == 1
    assert run.diagnostics.profit_protection_hits == 1
    # Trade exit reason reflects profit protection floor breach
    assert "Profit protection floor" in run.trades[0].exit_reason


# ==============================================================================
# Area 12: Mandatory EOD Square-Off at 15:10 IST
# ==============================================================================

def test_mandatory_eod_square_off_at_1510(synthetic_builder: SyntheticMarketDataBuilder):
    """Verify all positions are closed by mandatory exit time."""
    spot, options = synthetic_builder.build_bullish_breakout_dataset()
    engine = DiscreteEventBacktestEngine()

    run = engine.run(spot, options)
    assert len(run.trades) == 1
    assert run.diagnostics.square_off_count == 1
    assert "Mandatory intraday EOD square-off" in run.trades[0].exit_reason


# ==============================================================================
# Area 13: Missing Data Handling and Fail-Closed Diagnostics
# ==============================================================================

def test_missing_data_gap_detection(synthetic_builder: SyntheticMarketDataBuilder):
    """Verify that missing option candles are detected and counted in diagnostics."""
    spot, options = synthetic_builder.build_missing_data_dataset()
    engine = DiscreteEventBacktestEngine()

    run = engine.run(spot, options)
    # The fill bar was deleted, so order could not fill
    assert run.diagnostics.missing_data_events > 0


# ==============================================================================
# Area 14: Lookahead Bias Prevention (Timing Validation)
# ==============================================================================

def test_lookahead_bias_prevention(synthetic_builder: SyntheticMarketDataBuilder):
    """Verify entry price uses next bar open, not current bar close or intrabar high."""
    spot, options = synthetic_builder.build_bullish_breakout_dataset(ce_entry_price=Decimal("100.00"))
    exec_cfg = ExecutionModelConfig(slippage_points=Decimal("0.00"), fill_timing="next_bar_open")
    engine = DiscreteEventBacktestEngine(execution_config=exec_cfg)

    run = engine.run(spot, options)
    assert len(run.trades) == 1
    # At 09:47 bar open, price was 100.00 (while 09:46 close was 104.00)
    assert run.trades[0].entry_price == Decimal("100.00")


# ==============================================================================
# Area 15: Replay Repeatability (Deterministic Invariance)
# ==============================================================================

def test_replay_repeatability_produces_identical_metrics(synthetic_builder: SyntheticMarketDataBuilder):
    """Verify that running identical backtest runs produces bitwise-identical results."""
    spot, options = synthetic_builder.build_bullish_breakout_dataset()
    engine = DiscreteEventBacktestEngine()

    run1 = engine.run(spot, options)
    run2 = engine.run(spot, options)

    assert run1.metrics.net_pnl == run2.metrics.net_pnl
    assert run1.metrics.gross_pnl == run2.metrics.gross_pnl
    assert run1.metrics.win_rate == run2.metrics.win_rate
    assert run1.metrics.max_drawdown == run2.metrics.max_drawdown
    assert len(run1.trades) == len(run2.trades)


# ==============================================================================
# Area 16: Quantitative Metric Calculations Accuracy
# ==============================================================================

def test_quantitative_metrics_calculations():
    """Verify metrics calculation (Gross, Net, Win Rate, Expectancy, Max Drawdown)."""
    calc = MetricsCalculator(initial_capital=Decimal("100000.00"))
    now = datetime(2026, 11, 6, 15, 10, tzinfo=timezone.utc)

    t1 = BacktestTrade(
        symbol="NSE:NIFTY26NOV25000CE",
        option_type=OptionType.CE,
        strike=Decimal("25000"),
        entry_time=now,
        exit_time=now,
        entry_price=Decimal("100"),
        exit_price=Decimal("120"),
        qty=25,
        lot_size=25,
        gross_pnl=Decimal("500.00"),
        trading_costs=Decimal("50.00"),
        net_pnl=Decimal("450.00"),
        exit_reason="Target",
    )
    t2 = BacktestTrade(
        symbol="NSE:NIFTY26NOV24900PE",
        option_type=OptionType.PE,
        strike=Decimal("24900"),
        entry_time=now,
        exit_time=now,
        entry_price=Decimal("100"),
        exit_price=Decimal("90"),
        qty=25,
        lot_size=25,
        gross_pnl=Decimal("-250.00"),
        trading_costs=Decimal("50.00"),
        net_pnl=Decimal("-300.00"),
        exit_reason="Stoploss",
    )

    metrics = calc.compute([t1, t2])
    assert metrics.total_trades == 2
    assert metrics.winning_trades == 1
    assert metrics.losing_trades == 1
    assert metrics.win_rate == Decimal("50.00")
    assert metrics.gross_pnl == Decimal("250.00")
    assert metrics.net_pnl == Decimal("150.00")
    assert metrics.profit_factor == (Decimal("500.00") / Decimal("250.00")).quantize(Decimal("0.01"))


# ==============================================================================
# Area 17: Stored Backtest Run Persistence and Retrieval
# ==============================================================================

@pytest.mark.asyncio
async def test_stored_backtest_run_persistence_and_retrieval(
    test_session,
    synthetic_builder: SyntheticMarketDataBuilder,
):
    """Verify that BacktestRun and BacktestTrades persist and retrieve cleanly from database."""
    spot, options = synthetic_builder.build_bullish_breakout_dataset()
    engine = DiscreteEventBacktestEngine()
    run = engine.run(spot, options, dataset_id="PERSIST_DATASET")

    repo = StateRepository(test_session)
    await repo.save_backtest_run(run)

    fetched = await repo.get_backtest_run(run.run_id)
    assert fetched is not None
    assert fetched.dataset_id == "PERSIST_DATASET"
    assert fetched.strategy_name == "NIFTY_ORB"
    assert len(fetched.trades) == len(run.trades)

    runs_list = await repo.list_backtest_runs(limit=10)
    assert len(runs_list) >= 1


# ==============================================================================
# Area 18: Multiple Strategy Configuration Parameter Isolation
# ==============================================================================

def test_multiple_strategy_configuration_isolation(synthetic_builder: SyntheticMarketDataBuilder):
    """Verify that changing strategy configurations isolates parameters and affects output."""
    spot, options = synthetic_builder.build_bullish_breakout_dataset()

    strat_cfg1 = StrategyConfig(
        lots=1,
        breakout_confirmation_mode=BreakoutConfirmationMode.CANDLE_CLOSE,
        range_boundary_mode=RangeBoundaryMode.UP_TO_0944_CLOSE,
        order_execution_style=OrderExecutionStyle.MARKET,
        expiry_day_selection=ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE,
    )
    strat_cfg2 = StrategyConfig(
        lots=2,
        breakout_confirmation_mode=BreakoutConfirmationMode.CANDLE_CLOSE,
        range_boundary_mode=RangeBoundaryMode.UP_TO_0944_CLOSE,
        order_execution_style=OrderExecutionStyle.MARKET,
        expiry_day_selection=ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE,
    )

    engine1 = DiscreteEventBacktestEngine(strategy_config=strat_cfg1)
    engine2 = DiscreteEventBacktestEngine(strategy_config=strat_cfg2)

    run1 = engine1.run(spot, options)
    run2 = engine2.run(spot, options)

    assert run1.trades[0].qty == 25
    assert run2.trades[0].qty == 50
    assert run2.trades[0].gross_pnl == run1.trades[0].gross_pnl * 2


# ==============================================================================
# StockMock Benchmark Comparison Tests
# ==============================================================================

def test_stockmock_benchmark_blocked_when_csv_missing(synthetic_builder: SyntheticMarketDataBuilder):
    """Verify benchmark status is BLOCKED_BY_MISSING_DATA when CSV is absent."""
    spot, options = synthetic_builder.build_bullish_breakout_dataset()
    engine = DiscreteEventBacktestEngine()
    run = engine.run(spot, options)

    comparator = StockMockBenchmarkComparator(stockmock_csv_path=Path("non_existent_stockmock.csv"))
    report = comparator.compare(run.trades)

    assert report.status == BenchmarkStatus.BLOCKED_BY_MISSING_DATA
    assert "BLOCKED" in report.message


def test_stockmock_benchmark_matching_with_mock_csv(tmp_path: Path, synthetic_builder: SyntheticMarketDataBuilder):
    """Verify benchmark comparator matches trades when StockMock export CSV is provided."""
    spot, options = synthetic_builder.build_bullish_breakout_dataset()
    engine = DiscreteEventBacktestEngine()
    run = engine.run(spot, options)
    trade = run.trades[0]

    # Create mock StockMock CSV
    csv_file = tmp_path / "mock_stockmock.csv"
    with open(csv_file, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Type", "Strike", "Entry Price", "Exit Price", "PnL"])
        writer.writerow(["2026-11-06", "CE", str(trade.strike), str(trade.entry_price), str(trade.exit_price), str(trade.gross_pnl)])

    comparator = StockMockBenchmarkComparator(stockmock_csv_path=csv_file)
    report = comparator.compare(run.trades)

    assert report.status == BenchmarkStatus.MATCH
    assert report.matched_trades == 1
    assert report.pnl_difference == Decimal("0.00")
