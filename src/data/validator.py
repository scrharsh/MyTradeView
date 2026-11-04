"""Deterministic offline historical dataset validator and integrity verifier.

Enforces:
1. Strict schema validation for underlying index and option contracts.
2. Fixed-contract continuity (rejects mixed strikes, mixed expiries, mixed CE/PE).
3. Monotonic IST/UTC timestamp parsing and duplicate rejection.
4. Market session gap detection (09:15 to 15:30 IST) distinguishing session breaks from unexpected intraday gaps.
5. Cryptographic dataset fingerprinting (SHA-256).
6. Machine-readable manifest and human-readable quality report generation.
7. Anti-fabrication rules: zero synthetic fill, zero interpolation, zero volume fabrication.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from src.common.constants import OptionType
from src.common.exceptions import DataValidationError
from src.common.time_utils import IST_TZ, UTC_TZ, to_ist, to_utc
from src.core.models import Candle, OptionContract
from src.data.base import DataQualityReport

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------------
# 1. Dataset Manifest Model
# ------------------------------------------------------------------------------

class DatasetManifest(BaseModel):
    """Immutable, machine-readable manifest of a validated market dataset."""
    model_config = ConfigDict(frozen=True)

    manifest_version: str = "1.0.0"
    dataset_id: str
    fingerprint: str = Field(description="Deterministic SHA-256 hash of all canonical records")
    provenance: str = Field(description="Source file or vendor origin description")
    is_synthetic: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    trading_sessions: List[date] = Field(default_factory=list)
    underlying_symbols: List[str] = Field(default_factory=list)
    option_symbols: List[str] = Field(default_factory=list)
    lot_sizes: Dict[str, int] = Field(default_factory=dict, description="Authoritative lot size per symbol/date")
    total_bars: int = 0
    quality_reports: Dict[str, DataQualityReport] = Field(default_factory=dict)
    is_usable_for_backtest: bool = True
    validation_errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    def to_json(self, indent: int = 2) -> str:
        """Serialize manifest to JSON string."""
        return self.model_dump_json(indent=indent)


# ------------------------------------------------------------------------------
# 2. Offline Dataset Validator
# ------------------------------------------------------------------------------

class OfflineDatasetValidator:
    """
    Validates offline CSV and Parquet market data files for strategy backtesting.
    Enforces fixed-contract continuity, authoritative lot-size metadata,
    and lookahead-free chronological ordering.
    """

    # Exchange session boundary definitions (IST)
    MARKET_OPEN_TIME = time(9, 15, 0)
    MARKET_CLOSE_TIME = time(15, 30, 0)
    ORB_START_TIME = time(9, 16, 0)
    ORB_END_TIME = time(9, 45, 0)
    SQUARE_OFF_TIME = time(15, 10, 0)

    # Historical regulatory lot size transitions (NSE NIFTY)
    LOT_SIZE_RULES: List[Tuple[date, date, int]] = [
        (date(2000, 1, 1), date(2024, 4, 25), 50),
        (date(2024, 4, 26), date(2024, 11, 19), 25),
        (date(2024, 11, 20), date(2099, 12, 31), 75),
    ]

    def __init__(self) -> None:
        self.column_aliases: Dict[str, str] = {
            "datetime": "timestamp",
            "date_time": "timestamp",
            "date": "timestamp",
            "time": "timestamp",
            "ts": "timestamp",
            "candle_time": "timestamp",
            "open_price": "open",
            "o": "open",
            "first": "open",
            "high_price": "high",
            "h": "high",
            "max": "high",
            "low_price": "low",
            "l": "low",
            "min": "low",
            "close_price": "close",
            "c": "close",
            "last": "close",
            "ltp": "close",
            "vol": "volume",
            "v": "volume",
            "qty": "volume",
            "traded_qty": "volume",
            "open_interest": "open_interest",
            "openinterest": "open_interest",
            "oi": "open_interest",
            "strike_price": "strike",
            "strikeprice": "strike",
            "sp": "strike",
            "optiontype": "option_type",
            "opt_type": "option_type",
            "type": "option_type",
            "right": "option_type",
            "call_put": "option_type",
            "expiry": "expiry_date",
            "exp_date": "expiry_date",
            "expiration": "expiry_date",
            "underlying_symbol": "underlying",
            "index_name": "underlying",
        }

    # --------------------------------------------------------------------------
    # Column Normalization
    # --------------------------------------------------------------------------

    def normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize dataframe column headers using known aliases."""
        df = df.copy()
        renamed = {}
        for col in df.columns:
            cleaned = str(col).strip().lower()
            canonical = self.column_aliases.get(cleaned, cleaned)
            renamed[col] = canonical
        df = df.rename(columns=renamed)
        return df

    # --------------------------------------------------------------------------
    # Timestamp Normalization
    # --------------------------------------------------------------------------

    def parse_timestamp(self, raw_ts: Any) -> datetime:
        """Parse raw timestamp into timezone-aware UTC datetime."""
        if isinstance(raw_ts, (int, float)):
            epoch_val = float(raw_ts)
            if epoch_val > 1e11:  # Milliseconds
                epoch_val /= 1000.0
            return datetime.fromtimestamp(epoch_val, tz=timezone.utc)

        ts_str = str(raw_ts).strip()
        parsed = pd.to_datetime(ts_str).to_pydatetime()
        if parsed.tzinfo is None:
            # Exchange convention: Naive timestamps are strictly Indian Standard Time (IST)
            return IST_TZ.localize(parsed).astimezone(UTC_TZ)
        return parsed.astimezone(UTC_TZ)

    # --------------------------------------------------------------------------
    # Regulatory Lot-Size Resolution
    # --------------------------------------------------------------------------

    def get_authoritative_lot_size(self, session_date: date) -> int:
        """Resolve official NSE lot size based on session calendar date."""
        for start_dt, end_dt, lot_sz in self.LOT_SIZE_RULES:
            if start_dt <= session_date <= end_dt:
                return lot_sz
        return 25  # Fallback baseline

    # --------------------------------------------------------------------------
    # Validation: Underlying Index Data
    # --------------------------------------------------------------------------

    def validate_index_dataframe(
        self,
        df: pd.DataFrame,
        symbol: str = "NSE:NIFTY50-INDEX",
        source_name: str = "IndexDataFrame",
    ) -> Tuple[DataQualityReport, List[Candle]]:
        """
        Validate underlying index OHLCV dataframe.
        Volume is optional for index and is never fabricated if missing.
        """
        df = self.normalize_columns(df)
        diagnostic_messages: List[str] = []
        known_gaps: List[Dict[str, str]] = []
        duplicate_count = 0
        anomalous_count = 0
        valid_candles: List[Candle] = []
        seen_timestamps: Set[datetime] = set()

        # Check required columns
        required_cols = ["timestamp", "open", "high", "low", "close"]
        for col in required_cols:
            if col not in df.columns:
                raise DataValidationError(f"Missing required index column '{col}' in {source_name}. Found: {list(df.columns)}")

        has_volume = "volume" in df.columns

        for idx, row in df.iterrows():
            try:
                parsed_ts = self.parse_timestamp(row["timestamp"])
            except Exception as e:
                diagnostic_messages.append(f"Row {idx}: Malformed timestamp: {e}")
                anomalous_count += 1
                continue

            if parsed_ts in seen_timestamps:
                duplicate_count += 1
                diagnostic_messages.append(f"Row {idx}: Duplicate timestamp {parsed_ts.isoformat()} rejected.")
                continue

            try:
                op = Decimal(str(row["open"]))
                hi = Decimal(str(row["high"]))
                lo = Decimal(str(row["low"]))
                cl = Decimal(str(row["close"]))
                vol = int(row["volume"]) if has_volume and pd.notna(row["volume"]) else 0
            except (InvalidOperation, ValueError, TypeError) as e:
                diagnostic_messages.append(f"Row {idx} ({parsed_ts}): Malformed numeric OHLC: {e}")
                anomalous_count += 1
                continue

            # Price boundary verification
            if op <= 0 or hi <= 0 or lo <= 0 or cl <= 0 or vol < 0:
                diagnostic_messages.append(f"Row {idx} ({parsed_ts}): Non-positive price or negative volume.")
                anomalous_count += 1
                continue

            if hi < lo or hi < op or hi < cl or lo > op or lo > cl:
                diagnostic_messages.append(f"Row {idx} ({parsed_ts}): Boundary violation (O={op}, H={hi}, L={lo}, C={cl}).")
                anomalous_count += 1
                continue

            candle = Candle(
                symbol=symbol,
                timestamp=parsed_ts,
                open=op,
                high=hi,
                low=lo,
                close=cl,
                volume=vol,
            )
            valid_candles.append(candle)
            seen_timestamps.add(parsed_ts)

        valid_candles.sort(key=lambda c: c.timestamp)

        # Gap detection during market session (09:15 to 15:30 IST)
        gap_count = 0
        for i in range(1, len(valid_candles)):
            prev_ts = valid_candles[i - 1].timestamp
            curr_ts = valid_candles[i].timestamp
            delta = curr_ts - prev_ts
            prev_ist = to_ist(prev_ts)
            curr_ist = to_ist(curr_ts)

            # Intra-session gap check (same calendar date)
            if prev_ist.date() == curr_ist.date():
                if delta > timedelta(minutes=1):
                    gap_count += 1
                    known_gaps.append({
                        "from": prev_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
                        "to": curr_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
                        "duration_minutes": str(int(delta.total_seconds() // 60)),
                    })

        start_time = valid_candles[0].timestamp if valid_candles else None
        end_time = valid_candles[-1].timestamp if valid_candles else None

        report = DataQualityReport(
            symbol=symbol,
            source=source_name,
            start_time=start_time,
            end_time=end_time,
            total_records=len(df),
            valid_records=len(valid_candles),
            duplicate_records=duplicate_count,
            gap_intervals=gap_count,
            anomalous_records=anomalous_count,
            known_gaps=known_gaps,
            is_usable_for_backtest=(len(valid_candles) > 0 and anomalous_count == 0 and duplicate_count == 0),
            diagnostic_messages=diagnostic_messages,
        )

        return report, valid_candles

    # --------------------------------------------------------------------------
    # Validation: Option Contract Data & Fixed-Contract Continuity
    # --------------------------------------------------------------------------

    def validate_option_dataframe(
        self,
        df: pd.DataFrame,
        expected_symbol: Optional[str] = None,
        source_name: str = "OptionDataFrame",
    ) -> Tuple[DataQualityReport, List[Candle], Optional[OptionContract]]:
        """
        Validate option OHLCV dataframe and strictly enforce fixed-contract continuity.
        Rejects mixed strikes, mixed expiries, mixed CE/PE types, and missing contract identity.
        """
        df = self.normalize_columns(df)
        diagnostic_messages: List[str] = []
        known_gaps: List[Dict[str, str]] = []
        duplicate_count = 0
        anomalous_count = 0
        valid_candles: List[Candle] = []
        seen_timestamps: Set[datetime] = set()

        # Check required OHLC columns
        required_cols = ["timestamp", "open", "high", "low", "close"]
        for col in required_cols:
            if col not in df.columns:
                raise DataValidationError(f"Missing required option column '{col}' in {source_name}. Found: {list(df.columns)}")

        # ----------------------------------------------------------------------
        # Fixed-Contract Continuity Enforcement: Strike, Expiry, Option Type
        # ----------------------------------------------------------------------
        contract: Optional[OptionContract] = None
        has_strike_col = "strike" in df.columns
        has_expiry_col = "expiry_date" in df.columns
        has_type_col = "option_type" in df.columns
        has_symbol_col = "symbol" in df.columns

        # Verify fixed-strike continuity
        if has_strike_col:
            unique_strikes = df["strike"].dropna().unique()
            if len(unique_strikes) > 1:
                raise DataValidationError(
                    f"FIXED-CONTRACT VIOLATION in {source_name}: Multiple strike prices found in a single contract stream: "
                    f"{[str(s) for s in unique_strikes]}. Mixed strikes corrupt option delta/gamma accounting."
                )
            if len(unique_strikes) == 0:
                raise DataValidationError(f"Empty strike column in {source_name}.")
            contract_strike = Decimal(str(unique_strikes[0]))
        else:
            contract_strike = None

        # Verify fixed-expiry continuity
        if has_expiry_col:
            unique_expiries = df["expiry_date"].dropna().unique()
            if len(unique_expiries) > 1:
                raise DataValidationError(
                    f"FIXED-CONTRACT VIOLATION in {source_name}: Multiple expiry dates found in a single contract stream: "
                    f"{[str(e) for e in unique_expiries]}. Mixed expiries corrupt theta accounting."
                )
            if len(unique_expiries) == 0:
                raise DataValidationError(f"Empty expiry column in {source_name}.")
            raw_exp = unique_expiries[0]
            contract_expiry = pd.to_datetime(str(raw_exp)).date()
        else:
            contract_expiry = None

        # Verify option type purity (no mixed CE/PE)
        if has_type_col:
            unique_types = df["option_type"].dropna().astype(str).str.upper().str.strip().unique()
            if len(unique_types) > 1:
                raise DataValidationError(
                    f"FIXED-CONTRACT VIOLATION in {source_name}: Mixed option types found in a single stream: "
                    f"{list(unique_types)}. A contract must be purely CE or purely PE."
                )
            if len(unique_types) == 0:
                raise DataValidationError(f"Empty option_type column in {source_name}.")
            type_str = unique_types[0]
            if type_str not in ("CE", "PE", "CALL", "PUT"):
                raise DataValidationError(f"Invalid option type '{type_str}' in {source_name}. Expected CE or PE.")
            contract_type = OptionType.CE if type_str in ("CE", "CALL") else OptionType.PE
        else:
            contract_type = None

        # Determine authoritative symbol
        if has_symbol_col and len(df["symbol"].dropna().unique()) == 1:
            sym = str(df["symbol"].dropna().unique()[0]).strip()
        elif expected_symbol:
            sym = expected_symbol
        elif contract_strike and contract_expiry and contract_type:
            # Construct standard symbol
            exp_str = contract_expiry.strftime("%y%b").upper()
            sym = f"NSE:NIFTY{exp_str}{contract_strike:.0f}{contract_type.value}"
        else:
            raise DataValidationError(
                f"UNAMBIGUOUS CONTRACT IDENTITY MISSING in {source_name}: Could not resolve contract symbol, "
                "strike, expiry, and option type. Filename inference is forbidden."
            )

        # Resolve authoritative lot size
        sample_date = contract_expiry or date.today()
        lot_size = self.get_authoritative_lot_size(sample_date)

        if contract_strike and contract_expiry and contract_type:
            contract = OptionContract(
                symbol=sym,
                underlying="NSE:NIFTY50-INDEX",
                strike=contract_strike,
                option_type=contract_type,
                expiry_date=contract_expiry,
                lot_size=lot_size,
                tick_size=Decimal("0.05"),
            )

        has_volume = "volume" in df.columns
        has_oi = "open_interest" in df.columns

        for idx, row in df.iterrows():
            try:
                parsed_ts = self.parse_timestamp(row["timestamp"])
            except Exception as e:
                diagnostic_messages.append(f"Row {idx}: Malformed timestamp: {e}")
                anomalous_count += 1
                continue

            if parsed_ts in seen_timestamps:
                duplicate_count += 1
                diagnostic_messages.append(f"Row {idx}: Duplicate timestamp {parsed_ts.isoformat()} rejected.")
                continue

            try:
                op = Decimal(str(row["open"]))
                hi = Decimal(str(row["high"]))
                lo = Decimal(str(row["low"]))
                cl = Decimal(str(row["close"]))
                vol = int(row["volume"]) if has_volume and pd.notna(row["volume"]) else 0
            except (InvalidOperation, ValueError, TypeError) as e:
                diagnostic_messages.append(f"Row {idx} ({parsed_ts}): Malformed numeric option OHLC: {e}")
                anomalous_count += 1
                continue

            if op <= 0 or hi <= 0 or lo <= 0 or cl <= 0 or vol < 0:
                diagnostic_messages.append(f"Row {idx} ({parsed_ts}): Non-positive price or negative volume.")
                anomalous_count += 1
                continue

            if hi < lo or hi < op or hi < cl or lo > op or lo > cl:
                diagnostic_messages.append(f"Row {idx} ({parsed_ts}): Boundary violation (O={op}, H={hi}, L={lo}, C={cl}).")
                anomalous_count += 1
                continue

            candle = Candle(
                symbol=sym,
                timestamp=parsed_ts,
                open=op,
                high=hi,
                low=lo,
                close=cl,
                volume=vol,
            )
            valid_candles.append(candle)
            seen_timestamps.add(parsed_ts)

        valid_candles.sort(key=lambda c: c.timestamp)

        # Gap detection
        gap_count = 0
        for i in range(1, len(valid_candles)):
            prev_ts = valid_candles[i - 1].timestamp
            curr_ts = valid_candles[i].timestamp
            delta = curr_ts - prev_ts
            prev_ist = to_ist(prev_ts)
            curr_ist = to_ist(curr_ts)

            if prev_ist.date() == curr_ist.date():
                if delta > timedelta(minutes=1):
                    gap_count += 1
                    known_gaps.append({
                        "from": prev_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
                        "to": curr_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
                        "duration_minutes": str(int(delta.total_seconds() // 60)),
                    })

        start_time = valid_candles[0].timestamp if valid_candles else None
        end_time = valid_candles[-1].timestamp if valid_candles else None

        report = DataQualityReport(
            symbol=sym,
            source=source_name,
            start_time=start_time,
            end_time=end_time,
            total_records=len(df),
            valid_records=len(valid_candles),
            duplicate_records=duplicate_count,
            gap_intervals=gap_count,
            anomalous_records=anomalous_count,
            known_gaps=known_gaps,
            is_usable_for_backtest=(len(valid_candles) > 0 and anomalous_count == 0 and duplicate_count == 0),
            diagnostic_messages=diagnostic_messages,
        )

        return report, valid_candles, contract

    # --------------------------------------------------------------------------
    # Cryptographic Fingerprinting
    # --------------------------------------------------------------------------

    def compute_fingerprint(self, all_candles: List[Candle]) -> str:
        """
        Compute deterministic SHA-256 fingerprint over chronologically sorted candles.
        Guarantees that identical datasets yield exact identical hashes.
        """
        hasher = hashlib.sha256()
        sorted_candles = sorted(all_candles, key=lambda c: (c.timestamp, c.symbol))
        for c in sorted_candles:
            row_str = f"{c.symbol}|{c.timestamp.isoformat()}|{c.open:.2f}|{c.high:.2f}|{c.low:.2f}|{c.close:.2f}|{c.volume}\n"
            hasher.update(row_str.encode("utf-8"))
        return hasher.hexdigest()

    # --------------------------------------------------------------------------
    # Directory Validation Harness
    # --------------------------------------------------------------------------

    def validate_directory(
        self,
        dir_path: Path,
        provenance: str = "OFFLINE_DIRECTORY",
        is_synthetic: bool = False,
    ) -> Tuple[DatasetManifest, Dict[str, List[Candle]], Dict[str, OptionContract]]:
        """
        Validate an offline directory containing CSV or Parquet files.
        Discovers index and option files deterministically.
        """
        if not dir_path.exists() or not dir_path.is_dir():
            raise FileNotFoundError(f"Dataset directory not found: {dir_path}")

        all_candles: List[Candle] = []
        candles_by_symbol: Dict[str, List[Candle]] = {}
        contracts_by_symbol: Dict[str, OptionContract] = {}
        quality_reports: Dict[str, DataQualityReport] = {}
        validation_errors: List[str] = []
        warnings: List[str] = []

        # Supported file patterns
        csv_files = sorted(list(dir_path.glob("**/*.csv")))
        parquet_files = sorted(list(dir_path.glob("**/*.parquet")))
        data_files = csv_files + parquet_files

        if not data_files:
            return (
                DatasetManifest(
                    dataset_id="EMPTY_DATASET",
                    fingerprint="0" * 64,
                    provenance=provenance,
                    is_synthetic=is_synthetic,
                    is_usable_for_backtest=False,
                    validation_errors=[f"No CSV or Parquet data files found in directory {dir_path}"],
                ),
                {},
                {},
            )

        # Check for optional fo_contract_master.json
        master_path = dir_path / "fo_contract_master.json"
        contract_master_info: Dict[str, Any] = {}
        if master_path.exists():
            try:
                with open(master_path, "r", encoding="utf-8") as f:
                    contract_master_info = json.load(f)
            except Exception as e:
                warnings.append(f"Failed to read contract master at {master_path}: {e}")

        # Process each file
        for f in data_files:
            try:
                if f.suffix.lower() == ".csv":
                    df = pd.read_csv(f)
                else:
                    df = pd.read_parquet(f)
            except Exception as e:
                validation_errors.append(f"Failed to read file {f.name}: {e}")
                continue

            normalized_df = self.normalize_columns(df)
            is_index = (
                "index" in f.name.lower()
                or "spot" in f.name.lower()
                or ("strike" not in normalized_df.columns and "symbol" in normalized_df.columns and "INDEX" in str(normalized_df["symbol"].iloc[0]))
            )

            if is_index:
                try:
                    rep, c_list = self.validate_index_dataframe(df, source_name=f.name)
                    quality_reports[rep.symbol] = rep
                    candles_by_symbol[rep.symbol] = c_list
                    all_candles.extend(c_list)
                    if not rep.is_usable_for_backtest:
                        validation_errors.append(f"Index data in {f.name} failed quality check: {len(rep.diagnostic_messages)} anomalies.")
                except Exception as e:
                    validation_errors.append(f"Index validation failed for {f.name}: {e}")
            else:
                try:
                    rep, c_list, contract = self.validate_option_dataframe(df, source_name=f.name)
                    quality_reports[rep.symbol] = rep
                    candles_by_symbol[rep.symbol] = c_list
                    all_candles.extend(c_list)
                    if contract:
                        contracts_by_symbol[contract.symbol] = contract
                    if not rep.is_usable_for_backtest:
                        validation_errors.append(f"Option data in {f.name} failed quality check: {len(rep.diagnostic_messages)} anomalies.")
                except Exception as e:
                    validation_errors.append(f"Option validation failed for {f.name}: {e}")

        # Compute summary metrics
        total_bars = len(all_candles)
        fingerprint = self.compute_fingerprint(all_candles) if all_candles else ("0" * 64)
        dataset_id = f"{'SYNTH' if is_synthetic else 'HIST'}_{fingerprint[:16]}"

        all_dates: Set[date] = set()
        for c in all_candles:
            all_dates.add(to_ist(c.timestamp).date())
        sorted_sessions = sorted(list(all_dates))

        start_date = sorted_sessions[0] if sorted_sessions else None
        end_date = sorted_sessions[-1] if sorted_sessions else None

        # Build lot size map
        lot_sizes: Dict[str, int] = {}
        for sym, c in contracts_by_symbol.items():
            lot_sizes[sym] = c.lot_size

        is_usable = (
            len(validation_errors) == 0
            and total_bars > 0
            and any("INDEX" in s for s in candles_by_symbol.keys())
            and len(contracts_by_symbol) > 0
        )

        if not any("INDEX" in s for s in candles_by_symbol.keys()):
            validation_errors.append("Dataset lacks required underlying index data (NSE:NIFTY50-INDEX).")
            is_usable = False

        if len(contracts_by_symbol) == 0:
            validation_errors.append("Dataset lacks valid individual option contracts.")
            is_usable = False

        manifest = DatasetManifest(
            dataset_id=dataset_id,
            fingerprint=fingerprint,
            provenance=provenance,
            is_synthetic=is_synthetic,
            start_date=start_date,
            end_date=end_date,
            trading_sessions=sorted_sessions,
            underlying_symbols=[s for s in candles_by_symbol.keys() if "INDEX" in s],
            option_symbols=list(contracts_by_symbol.keys()),
            lot_sizes=lot_sizes,
            total_bars=total_bars,
            quality_reports=quality_reports,
            is_usable_for_backtest=is_usable,
            validation_errors=validation_errors,
            warnings=warnings,
        )

        return manifest, candles_by_symbol, contracts_by_symbol

    # --------------------------------------------------------------------------
    # Human-Readable Report Generation
    # --------------------------------------------------------------------------

    def generate_human_readable_report(self, manifest: DatasetManifest) -> str:
        """Format manifest as a Markdown quality verification report."""
        lines = [
            f"# Market Data Validation Report: {manifest.dataset_id}",
            f"**Provenance:** {manifest.provenance}",
            f"**Data Type:** {'SYNTHETIC FIXTURE' if manifest.is_synthetic else 'AUTHENTIC HISTORICAL'}",
            f"**SHA-256 Fingerprint:** `{manifest.fingerprint}`",
            f"**Status:** {'USABLE FOR BACKTEST' if manifest.is_usable_for_backtest else 'REJECTED'}",
            f"**Total Bars:** {manifest.total_bars:,}",
            f"**Sessions Covered:** {len(manifest.trading_sessions)} ({manifest.start_date} to {manifest.end_date})",
            "",
            "## Quality Summary per Symbol",
            "| Symbol | Total Rows | Valid Bars | Duplicates | Gaps | Anomalies | Usable? |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
        ]
        for sym, r in manifest.quality_reports.items():
            usable_str = "YES" if r.is_usable_for_backtest else "NO"
            lines.append(
                f"| `{sym}` | {r.total_records:,} | {r.valid_records:,} | {r.duplicate_records} | {r.gap_intervals} | {r.anomalous_records} | {usable_str} |"
            )

        if manifest.validation_errors:
            lines.extend(["", "## Validation Errors", *[f"- {e}" for e in manifest.validation_errors]])

        if manifest.warnings:
            lines.extend(["", "## Warnings", *[f"- {w}" for w in manifest.warnings]])

        return "\n".join(lines)

