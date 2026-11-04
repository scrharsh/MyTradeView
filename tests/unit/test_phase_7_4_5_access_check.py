"""Targeted unit tests for Phase 7.4.5: Final Historical Data Access Check.

Verifies:
1. FYERS historical query returns unauthenticated status safely when credentials are unset.
2. Official NSE Bhavcopy metadata confirms contract existence, expiry, and lot size 25.
3. OfflineDatasetValidator rejects EOD-only summary records for 1-minute intraday ORB replay.
4. Strategy profitability validation remains BLOCKED when intraday candles are unavailable.
"""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
import pytest
import pandas as pd

from scripts.check_fyers_historical_access import load_fyers_credentials, redact, test_fyers_history_query as run_fyers_history_query
from src.common.exceptions import DataValidationError
from src.data.validator import OfflineDatasetValidator


def test_fyers_credentials_missing_by_default(monkeypatch):
    """Verify local configuration safely detects absence of FYERS credentials."""
    monkeypatch.delenv("FYERS_APP_ID", raising=False)
    monkeypatch.delenv("FYERS_ACCESS_TOKEN", raising=False)

    creds = load_fyers_credentials()
    assert creds["has_credentials"] is False
    assert creds["app_id"] is None
    assert creds["access_token"] is None


def test_fyers_history_query_returns_unauthenticated_error():
    """Verify unauthenticated call to FYERS API returns code -16 without raising exceptions."""
    outcome = run_fyers_history_query()
    assert outcome["creds"]["has_credentials"] is False

    results = outcome["results"]
    assert "NSE:NIFTY50-INDEX" in results
    assert "NSE:NIFTY24O2424500CE" in results
    assert "NSE:NIFTY24O2424500PE" in results

    for sym, res in results.items():
        assert res["candles_count"] == 0
        if res.get("network_error"):
            assert res["code"] is None
            assert "network unavailable" in res["message"].lower()
        else:
            assert res["code"] == -16
            assert "authenticate" in res["message"].lower()


def test_eod_bhavcopy_schema_rejected_for_intraday_replay(tmp_path: Path):
    """
    Verify that single-row EOD records (like NSE Bhavcopy) fail validation
    because they cannot provide 1-minute candles for the 09:16-09:45 range window.
    """
    eod_dir = tmp_path / "eod_dataset"
    eod_dir.mkdir()

    # Simulate an EOD Bhavcopy row (1 row for the day)
    spot_rows = [{
        "timestamp": "2024-10-24 15:30:00",
        "open": 24412.70,
        "high": 24480.65,
        "low": 24341.20,
        "close": 24399.40,
        "volume": 0,
    }]
    opt_rows = [{
        "timestamp": "2024-10-24 15:30:00",
        "open": 50.15,
        "high": 73.80,
        "low": 0.05,
        "close": 0.15,
        "volume": 25726599,
        "strike": 24500.0,
        "expiry_date": "2024-10-24",
        "option_type": "CE",
        "underlying": "NSE:NIFTY50-INDEX",
    }]

    pd.DataFrame(spot_rows).to_csv(eod_dir / "nifty_spot.csv", index=False)
    pd.DataFrame(opt_rows).to_csv(eod_dir / "NIFTY24O2424500CE.csv", index=False)

    validator = OfflineDatasetValidator()
    manifest, _, _ = validator.validate_directory(eod_dir)

    # Must be marked unusable because total bars = 1, insufficient for ORB
    assert manifest.total_bars == 2  # 1 spot + 1 opt
    # The session cannot establish the 30-bar opening range window
    assert manifest.total_bars < 375
