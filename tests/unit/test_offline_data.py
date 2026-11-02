"""Unit tests for offline historical data provider supporting CSV and Parquet."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import pandas as pd
import pytest

from src.common.exceptions import DataValidationError
from src.core.models import Candle
from src.data.offline_provider import OfflineHistoricalDataProvider


@pytest.fixture
def sample_csv_path(tmp_path: Path) -> Path:
    """Create a temporary valid CSV file with 1-minute OHLCV candles."""
    file_path = tmp_path / "nifty_sample.csv"
    data = [
        "timestamp,open,high,low,close,volume",
        "2026-11-02 09:16:00,24500.0,24520.0,24490.0,24510.0,1000",
        "2026-11-02 09:17:00,24510.0,24530.0,24505.0,24525.0,1200",
        "2026-11-02 09:18:00,24525.0,24540.0,24515.0,24535.0,1100",
    ]
    file_path.write_text("\n".join(data), encoding="utf-8")
    return file_path


@pytest.fixture
def sample_parquet_path(tmp_path: Path) -> Path:
    """Create a temporary valid Parquet file."""
    file_path = tmp_path / "nifty_sample.parquet"
    df = pd.DataFrame([
        {
            "timestamp": "2026-11-02 09:16:00",
            "open": 24500.0,
            "high": 24520.0,
            "low": 24490.0,
            "close": 24510.0,
            "volume": 1000,
        },
        {
            "timestamp": "2026-11-02 09:17:00",
            "open": 24510.0,
            "high": 24530.0,
            "low": 24505.0,
            "close": 24525.0,
            "volume": 1200,
        },
    ])
    df.to_parquet(file_path)
    return file_path


def test_load_valid_csv_and_timestamp_normalization(sample_csv_path: Path):
    """Verify loading valid CSV data and converting IST timestamps to UTC."""
    provider = OfflineHistoricalDataProvider()
    report = provider.load_candles_from_csv("NSE:NIFTY50-INDEX", sample_csv_path, is_option=False)

    assert report.total_records == 3
    assert report.valid_records == 3
    assert report.duplicate_records == 0
    assert report.anomalous_records == 0
    assert report.is_usable_for_backtest is True

    # Timestamps should be in UTC: 09:16 IST = 03:46 UTC
    candles = provider._underlying_candles["NSE:NIFTY50-INDEX"]
    assert len(candles) == 3
    assert candles[0].timestamp.hour == 3
    assert candles[0].timestamp.minute == 46
    assert candles[0].open == Decimal("24500.0")


def test_load_valid_parquet(sample_parquet_path: Path):
    """Verify loading valid Parquet data."""
    provider = OfflineHistoricalDataProvider()
    report = provider.load_candles_from_parquet("NSE:NIFTY50-INDEX", sample_parquet_path, is_option=False)

    assert report.valid_records == 2
    assert report.is_usable_for_backtest is True


def test_duplicate_and_anomalous_candles_detection():
    """Verify detection of duplicate timestamps and invalid boundary rows."""
    provider = OfflineHistoricalDataProvider()
    df = pd.DataFrame([
        # Valid row 1
        {"timestamp": "2026-11-02 09:16:00", "open": 24500.0, "high": 24520.0, "low": 24490.0, "close": 24510.0, "volume": 100},
        # Duplicate timestamp (should be rejected)
        {"timestamp": "2026-11-02 09:16:00", "open": 24505.0, "high": 24525.0, "low": 24495.0, "close": 24515.0, "volume": 150},
        # Anomalous row: low > high
        {"timestamp": "2026-11-02 09:17:00", "open": 24500.0, "high": 24480.0, "low": 24530.0, "close": 24500.0, "volume": 100},
        # Anomalous row: negative price
        {"timestamp": "2026-11-02 09:18:00", "open": -100.0, "high": 24500.0, "low": -100.0, "close": 24500.0, "volume": 100},
        # Valid row 2
        {"timestamp": "2026-11-02 09:19:00", "open": 24510.0, "high": 24530.0, "low": 24505.0, "close": 24525.0, "volume": 120},
    ])

    report = provider.load_candles_from_dataframe("NSE:NIFTY50-INDEX", df, is_option=False)

    assert report.total_records == 5
    assert report.valid_records == 2
    assert report.duplicate_records == 1
    assert report.anomalous_records == 2
    assert report.is_usable_for_backtest is False
    assert len(report.diagnostic_messages) >= 3


def test_gap_detection_without_candle_fabrication():
    """Verify that gaps are flagged and never fabricated."""
    provider = OfflineHistoricalDataProvider()
    df = pd.DataFrame([
        {"timestamp": "2026-11-02 09:16:00", "open": 24500.0, "high": 24520.0, "low": 24490.0, "close": 24510.0, "volume": 100},
        # Missing 09:17, 09:18, 09:19 -> next is 09:20
        {"timestamp": "2026-11-02 09:20:00", "open": 24510.0, "high": 24530.0, "low": 24505.0, "close": 24525.0, "volume": 120},
    ])

    report = provider.load_candles_from_dataframe("NSE:NIFTY50-INDEX", df)
    assert report.gap_intervals == 1
    assert len(report.known_gaps) == 1
    assert report.known_gaps[0]["duration_minutes"] == "4"
    # Never fabricate missing candles: valid_records must strictly equal 2
    assert report.valid_records == 2


def test_deterministic_chronological_replay():
    """Verify interleaving underlying index and option contract candles deterministically."""
    provider = OfflineHistoricalDataProvider()

    df_index = pd.DataFrame([
        {"timestamp": "2026-11-02 09:16:00", "open": 24500.0, "high": 24520.0, "low": 24490.0, "close": 24510.0, "volume": 100},
        {"timestamp": "2026-11-02 09:18:00", "open": 24510.0, "high": 24530.0, "low": 24505.0, "close": 24525.0, "volume": 120},
    ])
    df_opt = pd.DataFrame([
        {"timestamp": "2026-11-02 09:16:00", "open": 100.0, "high": 105.0, "low": 98.0, "close": 102.0, "volume": 50},
        {"timestamp": "2026-11-02 09:17:00", "open": 102.0, "high": 108.0, "low": 101.0, "close": 107.0, "volume": 60},
    ])

    provider.load_candles_from_dataframe("NSE:NIFTY50-INDEX", df_index, is_option=False)
    provider.load_candles_from_dataframe("NSE:NIFTY26N0624500CE", df_opt, is_option=True)

    stream = provider.replay_chronological(["NSE:NIFTY50-INDEX", "NSE:NIFTY26N0624500CE"])
    assert len(stream) == 4

    # Check strict chronological ordering:
    # 09:16 (index), 09:16 (option), 09:17 (option), 09:18 (index)
    timestamps = [c.timestamp for c in stream]
    assert timestamps == sorted(timestamps)
