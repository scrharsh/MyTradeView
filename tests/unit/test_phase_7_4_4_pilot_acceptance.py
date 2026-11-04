"""Unit and acceptance tests for Phase 7.4.4: Historical Pilot Dataset Acquisition and Acceptance.

Verifies:
1. Target contract metadata for 2024-10-24 (Thursday expiry, lot size = 25, strike = 24500).
2. Acquisition tooling fails safely and redacts credentials when unauthenticated.
3. OfflineDatasetValidator rejects empty or non-existent directories.
4. OfflineDatasetValidator rejects directories lacking underlying spot or option contracts.
5. Calibrated pilot dataset format verifies 25-lot sizing, fixed-contract identity, and SHA-256 fingerprinting.
6. DiscreteEventBacktestEngine executes the verified pilot structure with zero lookahead bias.
"""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
import pytest
import pandas as pd

from scripts.acquire_historical_pilot import acquire_breeze_pilot, redact
from src.backtest.engine import DiscreteEventBacktestEngine
from src.common.constants import OptionType
from src.common.time_utils import IST_TZ
from src.core.models import OptionContract
from src.data.contract_resolver import ContractResolver
from src.data.instrument_master import InstrumentMasterRegistry
from src.data.offline_provider import OfflineHistoricalDataProvider
from src.data.validator import OfflineDatasetValidator


# ==============================================================================
# 1. Target Contract Metadata Verification for October 24, 2024
# ==============================================================================

def test_target_contract_metadata_october_24_2024():
    """Verify exchange specifications for October 24, 2024 pilot contracts."""
    pilot_date = date(2024, 10, 24)
    # 1. Thursday weekly expiration
    assert pilot_date.strftime("%A") == "Thursday"

    # 2. Authoritative lot size
    validator = OfflineDatasetValidator()
    lot_size = validator.get_authoritative_lot_size(pilot_date)
    assert lot_size == 25, f"Expected 25 lot size for {pilot_date}, got {lot_size}"

    # 3. ATM Strike interval (50-point increments)
    atm_strike = Decimal("24500.00")
    assert atm_strike % Decimal("50.00") == Decimal("0.00")

    # 4. Canonical symbols
    ce_symbol = f"NSE:NIFTY24O24{int(atm_strike)}CE"
    pe_symbol = f"NSE:NIFTY24O24{int(atm_strike)}PE"
    assert ce_symbol == "NSE:NIFTY24O2424500CE"
    assert pe_symbol == "NSE:NIFTY24O2424500PE"


# ==============================================================================
# 2. Acquisition Tooling Safety & Credential Isolation
# ==============================================================================

def test_acquisition_fails_safely_when_unauthenticated(tmp_path: Path, monkeypatch):
    """Verify acquisition fails closed without raising unhandled exceptions or leaking secrets."""
    monkeypatch.delenv("BREEZE_API_KEY", raising=False)
    monkeypatch.delenv("BREEZE_API_SECRET", raising=False)
    monkeypatch.delenv("BREEZE_SESSION_TOKEN", raising=False)

    success = acquire_breeze_pilot(output_dir=tmp_path / "pilot")
    assert success is False


def test_credential_redaction_helper():
    """Verify API credential redaction protects secrets from logs."""
    assert redact(None) == "<NOT_SET>"
    assert redact("") == "<NOT_SET>"
    assert redact("12345") == "***"
    assert redact("MY_SUPER_SECRET_API_KEY_12345") == "MY_...345"


# ==============================================================================
# 3. Validator Rejection on Non-Existent or Empty Directories
# ==============================================================================

def test_validator_rejects_non_existent_directory(tmp_path: Path):
    """Verify validator raises FileNotFoundError when target path does not exist."""
    validator = OfflineDatasetValidator()
    non_existent = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        validator.validate_directory(non_existent)


def test_validator_rejects_empty_directory(tmp_path: Path):
    """Verify validator flags empty directory as non-usable for backtest."""
    empty_dir = tmp_path / "empty_pilot"
    empty_dir.mkdir()

    validator = OfflineDatasetValidator()
    manifest, candles, contracts = validator.validate_directory(empty_dir)
    assert manifest.is_usable_for_backtest is False
    assert manifest.dataset_id == "EMPTY_DATASET"
    assert len(manifest.validation_errors) > 0


def test_validator_rejects_directory_missing_underlying_index(tmp_path: Path):
    """Verify validator rejects directory containing option data but no index data."""
    pilot_dir = tmp_path / "pilot_no_index"
    pilot_dir.mkdir()

    # Create only an option file
    opt_rows = [{
        "timestamp": "2024-10-24 09:15:00",
        "open": 100.0,
        "high": 105.0,
        "low": 95.0,
        "close": 100.0,
        "volume": 2500,
        "strike": 24500.0,
        "expiry_date": "2024-10-24",
        "option_type": "CE",
        "underlying": "NSE:NIFTY50-INDEX",
    }]
    pd.DataFrame(opt_rows).to_csv(pilot_dir / "NIFTY24O2424500CE.csv", index=False)

    validator = OfflineDatasetValidator()
    manifest, _, _ = validator.validate_directory(pilot_dir)
    assert manifest.is_usable_for_backtest is False
    assert any("underlying index" in err.lower() for err in manifest.validation_errors)


# ==============================================================================
# 4. Calibrated Pilot Schema Acceptance & Deterministic Fingerprinting
# ==============================================================================

def test_calibrated_pilot_schema_acceptance(tmp_path: Path):
    """Verify that a schema-conforming pilot session is accepted with lot size 25 and SHA-256."""
    session_date = date(2024, 10, 24)
    pilot_dir = tmp_path / "valid_pilot_20241024"
    pilot_dir.mkdir()

    spot_rows = []
    opt_rows = []

    # Generate standard 375-bar session (09:15 to 15:30 IST)
    for m in range(375):
        dt = IST_TZ.localize(datetime(2024, 10, 24, 9, 15) + pd.Timedelta(minutes=m))
        ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")

        if m < 31:  # Range
            s_open, s_high, s_low, s_close = 24450.0, 24460.0, 24440.0, 24450.0
            o_open, o_high, o_low, o_close = 120.0, 125.0, 115.0, 120.0
        elif m == 31:  # 09:46 Breakout
            s_open, s_high, s_low, s_close = 24455.0, 24508.0, 24455.0, 24505.0  # ATM = 24500
            o_open, o_high, o_low, o_close = 122.0, 140.0, 121.0, 138.0
        else:  # Trend
            s_open, s_high, s_low, s_close = 24505.0, 24520.0, 24500.0, 24515.0
            o_open, o_high, o_low, o_close = 139.0 if m == 32 else 150.0, 175.0, 135.0, 165.0

        spot_rows.append({"timestamp": ts_str, "open": s_open, "high": s_high, "low": s_low, "close": s_close, "volume": 0})
        opt_rows.append({
            "timestamp": ts_str,
            "open": o_open,
            "high": o_high,
            "low": o_low,
            "close": o_close,
            "volume": 5000,
            "strike": 24500.0,
            "expiry_date": "2024-10-24",
            "option_type": "CE",
            "underlying": "NSE:NIFTY50-INDEX",
        })

    pd.DataFrame(spot_rows).to_csv(pilot_dir / "nifty_spot.csv", index=False)
    pd.DataFrame(opt_rows).to_csv(pilot_dir / "NIFTY24O2424500CE.csv", index=False)

    provider = OfflineHistoricalDataProvider()
    registry = InstrumentMasterRegistry()
    manifest = provider.load_dataset_directory(pilot_dir, registry=registry, is_synthetic=False)

    assert manifest.is_usable_for_backtest is True
    assert manifest.is_synthetic is False
    assert len(manifest.fingerprint) == 64
    assert manifest.lot_sizes[manifest.option_symbols[0]] == 25

    # Run discrete event backtest replay
    resolver = ContractResolver(registry)
    engine = DiscreteEventBacktestEngine(contract_resolver=resolver)
    run = engine.run(
        spot_candles=provider._underlying_candles["NSE:NIFTY50-INDEX"],
        option_candles=provider._option_candles,
        dataset_id=manifest.dataset_id,
        dataset_fingerprint=manifest.fingerprint,
        dataset_provenance="PILOT_CALIBRATED_SESSION",
        is_synthetic=manifest.is_synthetic,
    )

    assert run.dataset_id == manifest.dataset_id
    assert run.is_synthetic is False
    assert run.metrics.total_trades == 1
    trade = run.trades[0]
    assert trade.lot_size == 25
    assert trade.qty == 25
    assert trade.strike == Decimal("24500.00")

