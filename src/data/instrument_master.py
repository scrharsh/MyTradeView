"""Authoritative Instrument Master parser and registry for FYERS derivatives."""

import csv
import hashlib
import io
import os
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.request import Request, urlopen
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from src.common.constants import OptionType
from src.common.exceptions import DataValidationError
from src.core.models import Instrument, OptionContract


class InstrumentMasterReport(BaseModel):
    """Validation report generated upon loading the instrument master."""
    model_config = ConfigDict(frozen=True)

    source_path: str
    total_records: int
    valid_instruments: int
    valid_options: int
    malformed_records: int
    duplicate_records: int
    underlying_symbols: List[str]
    diagnostic_messages: List[str] = Field(default_factory=list)


class InstrumentMasterRegistry:
    """
    Authoritative registry of tradable contracts parsed directly from official
    FYERS symbol master files (e.g. NSE_FO.csv). Guarantees that only verified,
    exchange-listed contracts with dynamic lot sizes are resolved.
    """

    def __init__(self) -> None:
        self._instruments: Dict[str, Instrument] = {}
        self._options: Dict[str, OptionContract] = {}
        # Key: (underlying, expiry_date, strike, option_type) -> OptionContract
        self._options_by_tuple: Dict[Tuple[str, date, Decimal, OptionType], OptionContract] = {}
        # Key: underlying -> sorted list of dates
        self._expiries_by_underlying: Dict[str, List[date]] = {}
        self._last_report: Optional[InstrumentMasterReport] = None
        self._source_sha256: Optional[str] = None
        self._source_url: Optional[str] = None
        self._source_loaded_at: Optional[datetime] = None

    @property
    def is_loaded(self) -> bool:
        """Check if registry contains loaded instruments."""
        return len(self._options) > 0 or len(self._instruments) > 0

    @property
    def symbols(self) -> List[str]:
        """Return all registered option symbols in deterministic order."""
        return sorted(self._options)

    @property
    def source_metadata(self) -> Dict[str, Optional[str]]:
        """Return non-sensitive provenance for the loaded symbol master."""
        return {
            "url": self._source_url,
            "sha256": self._source_sha256,
            "loaded_at": self._source_loaded_at.isoformat() if self._source_loaded_at else None,
        }

    def parse_csv(self, file_path_or_content: str | Path) -> InstrumentMasterReport:
        """
        Parse FYERS official symbol master CSV (supports both headerless 15-column format
        and header-based standard format).
        """
        diagnostic_messages: List[str] = []
        total_records = 0
        valid_options_count = 0
        malformed_count = 0
        duplicate_count = 0
        seen_symbols: Set[str] = set()

        if isinstance(file_path_or_content, Path):
            if not file_path_or_content.exists():
                raise FileNotFoundError(f"Instrument master file not found: {file_path_or_content}")
            with open(file_path_or_content, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            source_name = str(file_path_or_content)
        else:
            lines = file_path_or_content.strip().splitlines()
            source_name = "StringContent"

        if not lines:
            raise DataValidationError(f"Instrument master source '{source_name}' is empty.")

        reader = csv.reader(lines)
        first_row = next(reader, None)
        if first_row is None:
            raise DataValidationError("Empty CSV records in instrument master.")

        # Check if first row is a header
        has_header = False
        lower_row = [
            c.strip().lower().replace(" ", "_").replace("-", "_")
            for c in first_row
        ]
        if any(h in lower_row for h in ["symbol", "trading_symbol", "tradingsymbol", "fytoken"]):
            has_header = True
            header_map = {col: idx for idx, col in enumerate(lower_row)}
        else:
            header_map = {}
            # Reset reader to include first row as data
            reader = csv.reader(lines)

        for row_idx, row in enumerate(reader):
            total_records += 1
            if not row or len(row) < 7:
                malformed_count += 1
                diagnostic_messages.append(f"Row {row_idx}: Insufficient columns ({len(row)})")
                continue

            try:
                if has_header:
                    def column(*names: str, default: Optional[int] = None) -> str:
                        for name in names:
                            if name in header_map:
                                return row[header_map[name]].strip()
                        if default is None:
                            raise DataValidationError(f"Missing required symbol-master column: {names[0]}")
                        return row[default].strip()

                    sym = column("trading_symbol", "symbol", default=0)
                    underlying = column("underlying_symbol", "underlying", default=1)
                    strike_str = column("strike_price", "strike", default=2)
                    opt_type_str = column("option_type", default=3).upper()
                    expiry_str = column("expiry_date", "expiry", default=4)
                    lot_size_str = column("minimum_lot_size", "lot_size", default=5)
                    tick_size_str = column("tick_size", default=6) if "tick_size" in header_map else "0.05"
                else:
                    # FYERS official 15-column NSE_FO.csv format:
                    # 0: fy_token, 1: symbol_details, 2: inst_type, 3: lot_size, 4: tick_size,
                    # 5: isin, 6: trading_symbol, 7: update_date, 8: expiry_date,
                    # 9: strike_price, 10: option_type, 11: underlying, 12: underlying_fy_token...
                    if len(row) >= 12:
                        sym = row[6].strip()
                        lot_size_str = row[3].strip()
                        tick_size_str = row[4].strip()
                        expiry_str = row[8].strip()
                        strike_str = row[9].strip()
                        opt_type_str = row[10].strip().upper()
                        underlying = row[11].strip()
                    else:
                        malformed_count += 1
                        continue

                # Skip non-options (e.g. Futures or underlying entries in FO file)
                if opt_type_str not in ("CE", "PE"):
                    continue

                if sym in seen_symbols:
                    duplicate_count += 1
                    diagnostic_messages.append(f"Row {row_idx}: Duplicate trading symbol '{sym}'")
                    continue

                # Parse numerical and date values
                strike = Decimal(strike_str)
                lot_size = int(float(lot_size_str))
                tick_size = Decimal(tick_size_str) if tick_size_str else Decimal("0.05")

                if strike <= 0 or lot_size <= 0:
                    malformed_count += 1
                    diagnostic_messages.append(f"Row {row_idx} ({sym}): Non-positive strike or lot size")
                    continue

                # Parse expiry date (support epoch or yyyy-mm-dd or dd-mm-yyyy)
                if expiry_str.isdigit() and len(expiry_str) >= 9:
                    expiry_dt = datetime.fromtimestamp(int(expiry_str), tz=timezone.utc).date()
                else:
                    expiry_dt = pd.to_datetime(expiry_str).date()

                opt_type = OptionType.CE if opt_type_str == "CE" else OptionType.PE

                contract = OptionContract(
                    symbol=sym,
                    underlying=underlying,
                    strike=strike,
                    option_type=opt_type,
                    expiry_date=expiry_dt,
                    lot_size=lot_size,
                    tick_size=tick_size,
                )

                self._options[sym] = contract
                self._options_by_tuple[(underlying, expiry_dt, strike, opt_type)] = contract

                if underlying not in self._expiries_by_underlying:
                    self._expiries_by_underlying[underlying] = []
                if expiry_dt not in self._expiries_by_underlying[underlying]:
                    self._expiries_by_underlying[underlying].append(expiry_dt)

                seen_symbols.add(sym)
                valid_options_count += 1

            except Exception as e:
                malformed_count += 1
                diagnostic_messages.append(f"Row {row_idx}: Parse error: {e}")

        # Sort expiries chronologically
        for und in self._expiries_by_underlying:
            self._expiries_by_underlying[und].sort()

        report = InstrumentMasterReport(
            source_path=source_name,
            total_records=total_records,
            valid_instruments=len(self._instruments),
            valid_options=valid_options_count,
            malformed_records=malformed_count,
            duplicate_records=duplicate_count,
            underlying_symbols=list(self._expiries_by_underlying.keys()),
            diagnostic_messages=diagnostic_messages,
        )
        self._last_report = report
        return report

    @classmethod
    def load_official(
        cls,
        cache_path: str | Path,
        url: str = "https://public.fyers.in/sym_details/NSE_FO.csv",
        max_age_hours: float = 24.0,
        timeout_seconds: float = 20.0,
    ) -> "InstrumentMasterRegistry":
        """Download and validate the official FYERS NSE derivatives master.

        A cached file is accepted only while fresh. Downloads are validated in a
        temporary registry before replacing the cache, so malformed or partial
        responses cannot become authoritative state.
        """
        cache = Path(cache_path)
        cache.parent.mkdir(parents=True, exist_ok=True)
        content: Optional[str] = None
        if cache.exists():
            age_seconds = datetime.now(timezone.utc).timestamp() - cache.stat().st_mtime
            if age_seconds <= max_age_hours * 3600:
                content = cache.read_text(encoding="utf-8", errors="ignore")

        if content is None:
            request = Request(url, headers={"User-Agent": "MyTradeView/1.0"})
            try:
                with urlopen(request, timeout=timeout_seconds) as response:
                    raw = response.read()
            except Exception as exc:
                raise DataValidationError(
                    f"Official FYERS instrument master unavailable: {type(exc).__name__}"
                ) from exc
            if not raw:
                raise DataValidationError("Official FYERS instrument master returned an empty response.")
            content = raw.decode("utf-8-sig", errors="strict")

        candidate = cls()
        report = candidate.parse_csv(content)
        if report.valid_options == 0:
            raise DataValidationError("Official FYERS instrument master contains no valid option contracts.")
        if report.malformed_records > max(10, report.total_records // 10):
            raise DataValidationError(
                "Official FYERS instrument master failed validation: too many malformed records."
            )

        if not cache.exists() or content != cache.read_text(encoding="utf-8", errors="ignore"):
            temp_path = cache.with_suffix(cache.suffix + ".tmp")
            temp_path.write_text(content, encoding="utf-8")
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            os.replace(temp_path, cache)

        candidate._source_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        candidate._source_url = url
        candidate._source_loaded_at = datetime.now(timezone.utc)
        return candidate

    def register_option_contract(self, contract: OptionContract) -> None:
        """Register a validated OptionContract directly into the authoritative registry."""
        self._options[contract.symbol] = contract
        self._options_by_tuple[(contract.underlying, contract.expiry_date, contract.strike, contract.option_type)] = contract
        if contract.underlying not in self._expiries_by_underlying:
            self._expiries_by_underlying[contract.underlying] = []
        if contract.expiry_date not in self._expiries_by_underlying[contract.underlying]:
            self._expiries_by_underlying[contract.underlying].append(contract.expiry_date)
            self._expiries_by_underlying[contract.underlying].sort()

    def get_contract(
        self,
        underlying: str,
        expiry: date,
        strike: Decimal,
        option_type: OptionType,
    ) -> Optional[OptionContract]:
        """Look up verified contract by underlying, expiry, strike, and option type."""
        return self._options_by_tuple.get((underlying, expiry, strike, option_type))

    def get_contract_by_symbol(self, symbol: str) -> Optional[OptionContract]:
        """Look up verified option contract directly by trading symbol."""
        return self._options.get(symbol)

    def get_expiries(self, underlying: str) -> List[date]:
        """Get sorted list of available expiry dates for underlying."""
        return self._expiries_by_underlying.get(underlying, [])

    def get_strikes(self, underlying: str, expiry: date) -> List[Decimal]:
        """Get sorted list of all available strikes for given underlying and expiry."""
        strikes = {
            c.strike for c in self._options.values()
            if c.underlying == underlying and c.expiry_date == expiry
        }
        return sorted(list(strikes))
