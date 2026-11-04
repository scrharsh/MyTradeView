"""Unit tests for Instrument Master parser and Contract Resolver."""

from datetime import date
from decimal import Decimal
import pytest

from src.common.constants import ExpiryDaySelectionMode, OptionType
from src.common.exceptions import DataValidationError
from src.data.contract_resolver import ContractResolver
from src.data.instrument_master import InstrumentMasterRegistry


@pytest.fixture
def sample_nse_fo_csv() -> str:
    """
    Sample FYERS NSE_FO.csv contents containing NIFTY weekly and monthly option contracts
    with dynamic lot sizes and holiday-adjusted expiries.
    """
    return """trading_symbol,underlying_symbol,strike_price,option_type,expiry_date,minimum_lot_size,tick_size
NSE:NIFTY26N0524500CE,NSE:NIFTY50-INDEX,24500.0,CE,2026-11-05,25,0.05
NSE:NIFTY26N0524500PE,NSE:NIFTY50-INDEX,24500.0,PE,2026-11-05,25,0.05
NSE:NIFTY26N0524550CE,NSE:NIFTY50-INDEX,24550.0,CE,2026-11-05,25,0.05
NSE:NIFTY26N0524550PE,NSE:NIFTY50-INDEX,24550.0,PE,2026-11-05,25,0.05
NSE:NIFTY26N1224500CE,NSE:NIFTY50-INDEX,24500.0,CE,2026-11-12,25,0.05
NSE:NIFTY26N1224500PE,NSE:NIFTY50-INDEX,24500.0,PE,2026-11-12,25,0.05
NSE:NIFTY26NOV24500CE,NSE:NIFTY50-INDEX,24500.0,CE,2026-11-26,25,0.05
NSE:NIFTY26NOV24500PE,NSE:NIFTY50-INDEX,24500.0,PE,2026-11-26,25,0.05
NSE:BANKNIFTY26N0550000CE,NSE:NIFTYBANK-INDEX,50000.0,CE,2026-11-05,15,0.05
"""


@pytest.fixture
def sample_positional_fyers_csv() -> str:
    """
    Sample official positional 15-column format from FYERS NSE_FO.csv:
    0: fy_token, 1: symbol_details, 2: inst_type, 3: lot_size, 4: tick_size,
    5: isin, 6: trading_symbol, 7: update_date, 8: expiry_date,
    9: strike_price, 10: option_type, 11: underlying, 12: underlying_fy_token...
    """
    return """1011124110524500,NIFTY 05 NOV 24500 CALL,14,50,0.05,,NSE:NIFTY26N0524500CE,,2026-11-05,24500.0,CE,NSE:NIFTY50-INDEX,101000000026000,,
1011124110524500,NIFTY 05 NOV 24500 PUT,14,50,0.05,,NSE:NIFTY26N0524500PE,,2026-11-05,24500.0,PE,NSE:NIFTY50-INDEX,101000000026000,,
"""


def test_parse_instrument_master_header_format(sample_nse_fo_csv: str):
    """Verify parsing header-based instrument master CSV."""
    registry = InstrumentMasterRegistry()
    report = registry.parse_csv(sample_nse_fo_csv)

    assert report.valid_options == 9
    assert report.malformed_records == 0
    assert report.duplicate_records == 0
    assert "NSE:NIFTY50-INDEX" in report.underlying_symbols
    assert "NSE:NIFTYBANK-INDEX" in report.underlying_symbols

    expiries = registry.get_expiries("NSE:NIFTY50-INDEX")
    assert expiries == [date(2026, 11, 5), date(2026, 11, 12), date(2026, 11, 26)]


def test_parse_instrument_master_positional_format(sample_positional_fyers_csv: str):
    """Verify parsing official 15-column FYERS positional format."""
    registry = InstrumentMasterRegistry()
    report = registry.parse_csv(sample_positional_fyers_csv)

    assert report.valid_options == 2
    contract = registry.get_contract_by_symbol("NSE:NIFTY26N0524500CE")
    assert contract is not None
    assert contract.lot_size == 50  # Dynamic lot size verified!
    assert contract.strike == Decimal("24500.0")


def test_parse_official_header_aliases():
    registry = InstrumentMasterRegistry()
    report = registry.parse_csv(
        "Fytoken,Symbol Details,Exchange Instrument Type,Minimum Lot Size,Tick Size,"
        "Trading Symbol,Expiry Date,Strike Price,Option Type,Underlying Symbol\n"
        "1,NIFTY CALL,14,25,0.05,NSE:NIFTY26N0524500CE,2026-11-05,24500,CE,NSE:NIFTY50-INDEX\n"
    )
    assert report.valid_options == 1
    assert registry.get_contract_by_symbol("NSE:NIFTY26N0524500CE") is not None


def test_load_official_master_validates_and_caches(monkeypatch, tmp_path, sample_positional_fyers_csv):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return sample_positional_fyers_csv.encode()

    monkeypatch.setattr("src.data.instrument_master.urlopen", lambda *args, **kwargs: Response())
    cache = tmp_path / "NSE_FO.csv"
    registry = InstrumentMasterRegistry.load_official(cache_path=cache, url="https://example.test/NSE_FO.csv")

    assert cache.exists()
    assert registry.source_metadata["url"] == "https://example.test/NSE_FO.csv"
    assert registry.get_contract_by_symbol("NSE:NIFTY26N0524500CE") is not None


def test_load_official_rejects_malformed_master(monkeypatch, tmp_path):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b"symbol,underlying,strike,option_type,expiry,lot_size\nbroken"

    monkeypatch.setattr("src.data.instrument_master.urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(DataValidationError, match="no valid option"):
        InstrumentMasterRegistry.load_official(cache_path=tmp_path / "NSE_FO.csv")


def test_atm_strike_calculation():
    """Verify ATM strike rounding to nearest 50 points."""
    registry = InstrumentMasterRegistry()
    resolver = ContractResolver(registry, strike_interval=Decimal("50.00"))

    # Test rounding boundaries
    assert resolver.calculate_atm_strike(Decimal("24500.00")) == Decimal("24500.00")
    assert resolver.calculate_atm_strike(Decimal("24510.00")) == Decimal("24500.00")
    assert resolver.calculate_atm_strike(Decimal("24524.99")) == Decimal("24500.00")
    assert resolver.calculate_atm_strike(Decimal("24525.00")) == Decimal("24550.00")
    assert resolver.calculate_atm_strike(Decimal("24535.00")) == Decimal("24550.00")
    assert resolver.calculate_atm_strike(Decimal("24574.00")) == Decimal("24550.00")
    assert resolver.calculate_atm_strike(Decimal("24575.00")) == Decimal("24600.00")


def test_weekly_expiry_resolution(sample_nse_fo_csv: str):
    """Verify resolving upcoming weekly expiry on arbitrary trading days."""
    registry = InstrumentMasterRegistry()
    registry.parse_csv(sample_nse_fo_csv)
    resolver = ContractResolver(registry)

    # Monday 2026-11-02 -> Nearest upcoming weekly expiry is Thursday 2026-11-05
    resolved = resolver.resolve_weekly_expiry(
        underlying="NSE:NIFTY50-INDEX",
        session_date=date(2026, 11, 2),
    )
    assert resolved == date(2026, 11, 5)

    # Friday 2026-11-06 (after 11-05 expiry) -> Nearest upcoming weekly expiry is Thursday 2026-11-12
    resolved_next = resolver.resolve_weekly_expiry(
        underlying="NSE:NIFTY50-INDEX",
        session_date=date(2026, 11, 6),
    )
    assert resolved_next == date(2026, 11, 12)


def test_expiry_day_0dte_vs_next_weekly(sample_nse_fo_csv: str):
    """Verify expiry day behavior (0 DTE vs NEXT_WEEKLY)."""
    registry = InstrumentMasterRegistry()
    registry.parse_csv(sample_nse_fo_csv)
    resolver = ContractResolver(registry)

    # On expiry day: 2026-11-05
    # Option 1: CURRENT_WEEKLY_0DTE
    exp_0dte = resolver.resolve_weekly_expiry(
        underlying="NSE:NIFTY50-INDEX",
        session_date=date(2026, 11, 5),
        expiry_day_mode=ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE,
    )
    assert exp_0dte == date(2026, 11, 5)

    # Option 2: NEXT_WEEKLY
    exp_next = resolver.resolve_weekly_expiry(
        underlying="NSE:NIFTY50-INDEX",
        session_date=date(2026, 11, 5),
        expiry_day_mode=ExpiryDaySelectionMode.NEXT_WEEKLY,
    )
    assert exp_next == date(2026, 11, 12)


def test_holiday_adjusted_expiry_resolution():
    """Verify that holiday-adjusted expiries (e.g. Wednesday expiry due to Thursday holiday) are honored."""
    # When Thursday 2026-11-05 is a holiday, NSE schedules expiry on Wednesday 2026-11-04
    csv_data = """trading_symbol,underlying_symbol,strike_price,option_type,expiry_date,minimum_lot_size,tick_size
NSE:NIFTY26N0424500CE,NSE:NIFTY50-INDEX,24500.0,CE,2026-11-04,25,0.05
NSE:NIFTY26N0424500PE,NSE:NIFTY50-INDEX,24500.0,PE,2026-11-04,25,0.05
"""
    registry = InstrumentMasterRegistry()
    registry.parse_csv(csv_data)
    resolver = ContractResolver(registry)

    # Resolving on Monday 2026-11-02 -> correctly resolves Wednesday 2026-11-04 from actual listed data!
    resolved = resolver.resolve_weekly_expiry("NSE:NIFTY50-INDEX", date(2026, 11, 2))
    assert resolved == date(2026, 11, 4)


def test_resolve_atm_contract_complete(sample_nse_fo_csv: str):
    """Verify end-to-end resolution of ATM Call and Put option contracts with dynamic lot size."""
    registry = InstrumentMasterRegistry()
    registry.parse_csv(sample_nse_fo_csv)
    resolver = ContractResolver(registry)

    # Spot = 24520.0 on Monday 2026-11-02 -> ATM strike = 24500.0, Expiry = 2026-11-05
    contract_ce = resolver.resolve_atm_contract(
        underlying="NSE:NIFTY50-INDEX",
        spot_price=Decimal("24520.00"),
        option_type=OptionType.CE,
        session_date=date(2026, 11, 2),
    )
    assert contract_ce.symbol == "NSE:NIFTY26N0524500CE"
    assert contract_ce.strike == Decimal("24500.0")
    assert contract_ce.lot_size == 25  # From master record
    assert contract_ce.expiry_date == date(2026, 11, 5)

    contract_pe = resolver.resolve_atm_contract(
        underlying="NSE:NIFTY50-INDEX",
        spot_price=Decimal("24520.00"),
        option_type=OptionType.PE,
        session_date=date(2026, 11, 2),
    )
    assert contract_pe.symbol == "NSE:NIFTY26N0524500PE"
    assert contract_pe.strike == Decimal("24500.0")


def test_missing_contract_fails_closed(sample_nse_fo_csv: str):
    """Verify that unlisted strike or expired underlying raises DataValidationError."""
    registry = InstrumentMasterRegistry()
    registry.parse_csv(sample_nse_fo_csv)
    resolver = ContractResolver(registry)

    # Strike 28000 does not exist in sample CSV -> Must fail closed!
    with pytest.raises(DataValidationError) as exc_info:
        resolver.resolve_atm_contract(
            underlying="NSE:NIFTY50-INDEX",
            spot_price=Decimal("28000.00"),
            option_type=OptionType.CE,
            session_date=date(2026, 11, 2),
        )
    assert "FAILED TO RESOLVE CONTRACT" in str(exc_info.value)
