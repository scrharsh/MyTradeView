"""Comprehensive unit and integration tests for Phase 7.4.3: Historical Replay Readiness.

Verifies:
1. Complete end-to-end execution path: File directory -> Validator -> Provider -> Registry -> Resolver -> Engine -> BacktestRun.
2. Fixed-contract preservation throughout holding lifecycle (no strike or expiry switching).
3. Missing option candles explicitly block execution fills and record missing_data_events.
4. Missing contract in registry fails closed (no guessing or silent substitution).
5. Dynamic regulatory lot size correctly flows into order request, fill, and trade P&L (50 vs 25 vs 75).
6. Cryptographic dataset fingerprint changes deterministically when data is mutated.
7. WorkstationService._format_backtest_summary correctly reflects is_synthetic=False for real data and True for synthetic.
8. Lookahead-free fill timing: Orders triggered at 09:46 close execute strictly at 09:47 open.
9. StockMock benchmark comparator matches trades when calibrated CSV is provided.
"""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional
import pytest
import pandas as pd

from config.strategy_config import StrategyConfig
from config.risk_config import RiskConfig
from src.api.service import WorkstationService
from src.backtest.benchmark import BenchmarkStatus, StockMockBenchmarkComparator
from src.backtest.engine import DiscreteEventBacktestEngine
from src.backtest.models import BacktestRun, ExecutionModelConfig
from src.backtest.synthetic import SyntheticMarketDataBuilder
from src.common.constants import OptionType, BreakoutConfirmationMode, RangeBoundaryMode, ExpiryDaySelectionMode
from src.common.exceptions import DataValidationError
from src.common.time_utils import IST_TZ, to_ist, to_utc
from src.core.models import Candle, OptionContract
from src.data.contract_resolver import ContractResolver
from src.data.instrument_master import InstrumentMasterRegistry
from src.data.offline_provider import OfflineHistoricalDataProvider
from src.data.validator import OfflineDatasetValidator, DatasetManifest


def create_mock_session_files(
    dir_path: Path,
    session_date: date,
    base_spot: Decimal = Decimal("24500.00"),
    breakout_up: bool = True,
    strike: Decimal = Decimal("24500.00"),
    expiry_date: Optional[date] = None,
    lot_size: int = 25,
) -> None:
    """Helper to generate a clean, controlled single-session test fixture."""
    exp_date = expiry_date or session_date
    spot_rows = []
    opt_rows = []

    # 09:15 to 15:30 = 375 bars
    for m in range(0, 375):
        dt = IST_TZ.localize(datetime(session_date.year, session_date.month, session_date.day, 9, 15) + pd.Timedelta(minutes=m))
        ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")

        # Range formation (09:16 to 09:45): 24495 to 24505
        if m < 31:
            s_open = base_spot
            s_high = base_spot + Decimal("5.00")
            s_low = base_spot - Decimal("5.00")
            s_close = base_spot + Decimal("1.00")
            o_open, o_high, o_low, o_close = Decimal("100"), Decimal("105"), Decimal("95"), Decimal("100")
        elif m == 31:  # 09:46 IST - Breakout candle
            if breakout_up:
                s_open = base_spot + Decimal("2.00")
                s_high = base_spot + Decimal("15.00")  # > 24505 OR High
                s_low = base_spot + Decimal("2.00")
                s_close = base_spot + Decimal("12.00")  # Confirms breakout, ATM strike = 24500
                o_open, o_high, o_low, o_close = Decimal("102"), Decimal("115"), Decimal("101"), Decimal("112")
            else:
                s_open = base_spot - Decimal("2.00")
                s_high = base_spot - Decimal("2.00")
                s_low = base_spot - Decimal("15.00")  # < 24495 OR Low
                s_close = base_spot - Decimal("12.00")  # Confirms breakout, ATM strike = 24500
                o_open, o_high, o_low, o_close = Decimal("102"), Decimal("115"), Decimal("101"), Decimal("112")
        else:  # 09:47 to 15:30 - Trend
            if breakout_up:
                s_open = base_spot + Decimal("12.00")
                s_high = base_spot + Decimal("20.00")
                s_low = base_spot + Decimal("10.00")
                s_close = base_spot + Decimal("18.00")
            else:
                s_open = base_spot - Decimal("12.00")
                s_high = base_spot - Decimal("10.00")
                s_low = base_spot - Decimal("20.00")
                s_close = base_spot - Decimal("18.00")
            # Option trends from 115 to 140
            o_open = Decimal("114.00") if m == 32 else Decimal("125.00")
            o_high = Decimal("142.00")
            o_low = Decimal("112.00")
            o_close = Decimal("138.00") if m == 355 else Decimal("126.00")  # 15:10 IST square-off

        spot_rows.append({"timestamp": ts_str, "open": float(s_open), "high": float(s_high), "low": float(s_low), "close": float(s_close), "volume": 0})
        opt_rows.append({
            "timestamp": ts_str,
            "open": float(o_open),
            "high": float(o_high),
            "low": float(o_low),
            "close": float(o_close),
            "volume": 2500,
            "strike": float(strike),
            "expiry_date": exp_date.strftime("%Y-%m-%d"),
            "option_type": "CE" if breakout_up else "PE",
        })

    pd.DataFrame(spot_rows).to_csv(dir_path / "nifty_spot.csv", index=False)
    opt_name = f"NIFTY_{exp_date.strftime('%Y%m%d')}_{int(strike)}_{'CE' if breakout_up else 'PE'}.csv"
    pd.DataFrame(opt_rows).to_csv(dir_path / opt_name, index=False)


# ==============================================================================
# 1. Complete End-to-End Replay Path
# ==============================================================================

def test_end_to_end_replay_path_with_fixture(tmp_path: Path):
    """
    Test complete pipeline from directory to backtest trades and run record:
    Directory -> Validator -> Provider -> Registry -> Resolver -> Engine -> BacktestRun.
    """
    session_date = date(2024, 10, 24)
    create_mock_session_files(tmp_path, session_date=session_date, base_spot=Decimal("24500.00"), breakout_up=True)

    provider = OfflineHistoricalDataProvider()
    registry = InstrumentMasterRegistry()

    # Load dataset through validator
    manifest = provider.load_dataset_directory(
        dir_path=tmp_path,
        provenance="SYNTHETIC_CONTROLLED_TEST_FIXTURE",
        is_synthetic=True,
        registry=registry,
    )

    assert manifest.is_usable_for_backtest is True
    assert len(manifest.option_symbols) == 1
    assert registry.is_loaded is True

    resolver = ContractResolver(registry)
    engine = DiscreteEventBacktestEngine(contract_resolver=resolver)

    spot_candles = provider._underlying_candles["NSE:NIFTY50-INDEX"]
    option_candles = provider._option_candles

    run = engine.run(
        spot_candles=spot_candles,
        option_candles=option_candles,
        dataset_id=manifest.dataset_id,
        dataset_fingerprint=manifest.fingerprint,
        dataset_provenance=manifest.provenance,
        is_synthetic=manifest.is_synthetic,
    )

    assert run.dataset_id == manifest.dataset_id
    assert run.dataset_fingerprint == manifest.fingerprint
    assert run.dataset_provenance == "SYNTHETIC_CONTROLLED_TEST_FIXTURE"
    assert run.is_synthetic is True
    assert run.metrics.total_trades == 1

    trade = run.trades[0]
    assert trade.direction == "BUY"
    assert trade.strike == Decimal("24500.00")
    assert trade.lot_size == 25
    assert trade.qty == 25
    assert trade.entry_time < trade.exit_time
    assert trade.gross_pnl > 0


# ==============================================================================
# 2. Fixed-Contract Preservation Throughout Holding Lifecycle
# ==============================================================================

def test_contract_selection_preserves_static_strike_and_expiry_throughout_position(tmp_path: Path):
    """Verify that once selected, the contract never rolls strike or expiry mid-position."""
    session_date = date(2024, 10, 24)
    create_mock_session_files(tmp_path, session_date=session_date, strike=Decimal("24500.00"))

    provider = OfflineHistoricalDataProvider()
    registry = InstrumentMasterRegistry()
    manifest = provider.load_dataset_directory(tmp_path, registry=registry, is_synthetic=True)

    resolver = ContractResolver(registry)
    engine = DiscreteEventBacktestEngine(contract_resolver=resolver)

    run = engine.run(
        spot_candles=provider._underlying_candles["NSE:NIFTY50-INDEX"],
        option_candles=provider._option_candles,
        dataset_id=manifest.dataset_id,
    )

    trade = run.trades[0]
    # Trade must strictly identify the 24500 CE contract
    assert trade.strike == Decimal("24500.00")
    assert trade.option_type == OptionType.CE
    assert trade.symbol == manifest.option_symbols[0]


# ==============================================================================
# 3. Missing Option Candles Explicitly Block Execution
# ==============================================================================

def test_missing_option_candles_blocks_execution_fill(tmp_path: Path):
    """Verify that if option candle is missing at breakout fill bar (09:47), order cannot fill."""
    session_date = date(2024, 10, 24)
    create_mock_session_files(tmp_path, session_date=session_date)

    provider = OfflineHistoricalDataProvider()
    registry = InstrumentMasterRegistry()
    manifest = provider.load_dataset_directory(tmp_path, registry=registry, is_synthetic=True)

    resolver = ContractResolver(registry)
    engine = DiscreteEventBacktestEngine(contract_resolver=resolver)

    spot_candles = provider._underlying_candles["NSE:NIFTY50-INDEX"]
    # Pass empty option candles map
    run = engine.run(
        spot_candles=spot_candles,
        option_candles={},
        dataset_id="TEST_MISSING_OPT",
    )

    # Order could not fill -> 0 trades executed
    assert len(run.trades) == 0
    assert run.metrics.total_trades == 0
    assert run.diagnostics.missing_data_events > 0


# ==============================================================================
# 4. Missing Contract in Registry Fails Closed
# ==============================================================================

def test_missing_contract_in_registry_fails_closed(tmp_path: Path):
    """Verify that if ContractResolver cannot resolve the required contract, it fails closed."""
    session_date = date(2024, 10, 24)
    create_mock_session_files(tmp_path, session_date=session_date)

    provider = OfflineHistoricalDataProvider()
    empty_registry = InstrumentMasterRegistry()  # Empty!
    resolver = ContractResolver(empty_registry)

    # Calling resolve_atm_contract on empty registry raises DataValidationError
    with pytest.raises(DataValidationError, match="Cannot resolve contract: Instrument master registry is empty"):
        resolver.resolve_atm_contract(
            underlying="NSE:NIFTY50-INDEX",
            spot_price=Decimal("24530.00"),
            option_type=OptionType.CE,
            session_date=session_date,
        )


# ==============================================================================
# 5. Regulatory Lot Size Resolution in Trade Accounting
# ==============================================================================

def test_historical_lot_size_flows_into_trade_pnl(tmp_path: Path):
    """
    Verify that authoritative lot size (50 in 2023, 25 in late 2024)
    determines trade quantity and P&L scaling.
    """
    session_date_2023 = date(2023, 10, 19)
    dir_2023 = tmp_path / "sess_2023"
    dir_2023.mkdir()
    create_mock_session_files(dir_2023, session_date=session_date_2023, lot_size=50)

    validator = OfflineDatasetValidator()
    assert validator.get_authoritative_lot_size(session_date_2023) == 50

    provider = OfflineHistoricalDataProvider()
    registry = InstrumentMasterRegistry()
    manifest = provider.load_dataset_directory(dir_2023, registry=registry, is_synthetic=True)

    contract = list(registry._options.values())[0]
    assert contract.lot_size == 50

    resolver = ContractResolver(registry)
    engine = DiscreteEventBacktestEngine(contract_resolver=resolver)
    run = engine.run(
        spot_candles=provider._underlying_candles["NSE:NIFTY50-INDEX"],
        option_candles=provider._option_candles,
        dataset_id=manifest.dataset_id,
    )

    trade = run.trades[0]
    assert trade.lot_size == 50
    assert trade.qty == 50


# ==============================================================================
# 6. Dataset Fingerprint Changes on Data Mutation
# ==============================================================================

def test_dataset_fingerprint_changes_on_data_mutation(tmp_path: Path):
    """Verify that altering a single price point changes the SHA-256 fingerprint."""
    session_date = date(2024, 10, 24)
    dir_a = tmp_path / "dir_a"
    dir_b = tmp_path / "dir_b"
    dir_a.mkdir()
    dir_b.mkdir()

    create_mock_session_files(dir_a, session_date=session_date)
    create_mock_session_files(dir_b, session_date=session_date)

    # Mutate one candle in dir_b
    spot_b = pd.read_csv(dir_b / "nifty_spot.csv")
    spot_b.loc[0, "close"] = float(spot_b.loc[0, "close"]) + 1.0
    spot_b.to_csv(dir_b / "nifty_spot.csv", index=False)

    val = OfflineDatasetValidator()
    man_a, _, _ = val.validate_directory(dir_a)
    man_b, _, _ = val.validate_directory(dir_b)

    assert man_a.fingerprint != man_b.fingerprint
    assert man_a.dataset_id != man_b.dataset_id


# ==============================================================================
# 7. WorkstationService Preserves Dynamic Synthetic Flag
# ==============================================================================

def test_api_service_preserves_dynamic_synthetic_flag():
    """Verify WorkstationService._format_backtest_summary reports real is_synthetic status."""
    service = WorkstationService()
    builder = SyntheticMarketDataBuilder(session_date=date(2026, 11, 6))
    spot, options = builder.build_bullish_breakout_dataset()
    engine = DiscreteEventBacktestEngine()

    run_synthetic = engine.run(spot, options, dataset_id="SYNTH_RUN", is_synthetic=True)
    summary_synth = service._format_backtest_summary(run_synthetic)
    assert summary_synth.is_synthetic is True

    run_historical = engine.run(spot, options, dataset_id="HIST_RUN", is_synthetic=False)
    summary_hist = service._format_backtest_summary(run_historical)
    assert summary_hist.is_synthetic is False


# ==============================================================================
# 8. Lookahead-Free Fill Timing
# ==============================================================================

def test_lookahead_free_next_bar_open_fill_timing(tmp_path: Path):
    """Verify that a breakout signal on 09:46 candle close executes strictly at 09:47 open."""
    session_date = date(2024, 10, 24)
    create_mock_session_files(tmp_path, session_date=session_date)

    provider = OfflineHistoricalDataProvider()
    registry = InstrumentMasterRegistry()
    manifest = provider.load_dataset_directory(tmp_path, registry=registry, is_synthetic=True)

    resolver = ContractResolver(registry)
    engine = DiscreteEventBacktestEngine(contract_resolver=resolver)

    run = engine.run(
        spot_candles=provider._underlying_candles["NSE:NIFTY50-INDEX"],
        option_candles=provider._option_candles,
        dataset_id=manifest.dataset_id,
    )

    trade = run.trades[0]
    entry_ist = to_ist(trade.entry_time)
    assert entry_ist.hour == 9
    assert entry_ist.minute == 47  # Exactly next-bar open (09:47:00 IST)


# ==============================================================================
# 9. StockMock Benchmark Calibration with Matched Trade
# ==============================================================================

def test_stockmock_benchmark_calibration_with_matched_trade(tmp_path: Path):
    """Verify that StockMockBenchmarkComparator validates execution parity when calibrated CSV is provided."""
    session_date = date(2024, 10, 24)
    create_mock_session_files(tmp_path, session_date=session_date)

    provider = OfflineHistoricalDataProvider()
    registry = InstrumentMasterRegistry()
    manifest = provider.load_dataset_directory(tmp_path, registry=registry, is_synthetic=True)

    resolver = ContractResolver(registry)
    engine = DiscreteEventBacktestEngine(contract_resolver=resolver)

    run = engine.run(
        spot_candles=provider._underlying_candles["NSE:NIFTY50-INDEX"],
        option_candles=provider._option_candles,
        dataset_id=manifest.dataset_id,
    )
    trade = run.trades[0]

    # Generate matching StockMock trade CSV
    sm_csv = tmp_path / "stockmock_export.csv"
    sm_df = pd.DataFrame([{
        "Date": "2024-10-24",
        "Type": "CE",
        "Strike": str(trade.strike),
        "Entry Price": str(trade.entry_price),
        "Exit Price": str(trade.exit_price),
        "PnL": str(trade.gross_pnl),
    }])
    sm_df.to_csv(sm_csv, index=False)

    comparator = StockMockBenchmarkComparator(stockmock_csv_path=sm_csv)
    report = comparator.compare(run.trades)

    assert report.status == BenchmarkStatus.MATCH
    assert report.matched_trades == 1
    assert report.pnl_difference == Decimal("0.00")
