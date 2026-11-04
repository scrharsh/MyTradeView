"""Authoritative Instrument Master parser and registry for FYERS derivatives."""

import csv
import io
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
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

    @property
    def is_loaded(self) -> bool:
        """Check if registry contains loaded instruments."""
        return len(self._options) > 0 or len(self._instruments) > 0

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
        lower_row = [c.strip().lower() for c in first_row]
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
                    sym = row[header_map.get("trading_symbol", header_map.get("symbol", 0))].strip()
                    underlying = row[header_map.get("underlying_symbol", header_map.get("underlying", 1))].strip()
                    strike_str = row[header_map.get("strike_price", header_map.get("strike", 2))].strip()
                    opt_type_str = row[header_map.get("option_type", 3)].strip().upper()
                    expiry_str = row[header_map.get("expiry_date", header_map.get("expiry", 4))].strip()
                    lot_size_str = row[header_map.get("minimum_lot_size", header_map.get("lot_size", 5))].strip()
                    tick_size_str = row[header_map.get("tick_size", 6)].strip() if "tick_size" in header_map else "0.05"
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
