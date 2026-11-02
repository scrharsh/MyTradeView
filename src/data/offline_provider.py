"""Offline historical data provider supporting validated CSV and Parquet files."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Set, Tuple
import pandas as pd
import pytz

from src.common.exceptions import DataValidationError
from src.common.time_utils import IST_TZ, UTC_TZ, to_ist, to_utc
from src.core.models import Candle, Instrument, MarketTick, OptionContract
from src.data.base import DataQualityReport, MarketDataProvider


class OfflineHistoricalDataProvider(MarketDataProvider):
    """
    Offline market data provider loading, validating, and replaying historical
    underlying and derivative market data from CSV and Parquet files.
    """

    def __init__(self) -> None:
        # Separate stores for underlying index data and option contract data
        self._underlying_candles: Dict[str, List[Candle]] = {}
        self._option_candles: Dict[str, List[Candle]] = {}
        self._quality_reports: Dict[str, DataQualityReport] = {}
        self._instruments: Dict[str, Instrument] = {}
        self._option_contracts: Dict[str, OptionContract] = {}

    # --------------------------------------------------------------------------
    # 1. File Ingestion & Data Quality Validation
    # --------------------------------------------------------------------------

    def load_candles_from_dataframe(
        self,
        symbol: str,
        df: pd.DataFrame,
        is_option: bool = False,
        source_name: str = "DataFrame",
    ) -> DataQualityReport:
        """
        Validate and ingest OHLCV data from a pandas DataFrame into memory.
        Detects duplicates, validates price boundaries, checks for gaps,
        and never fabricates missing bars.
        """
        diagnostic_messages: List[str] = []
        known_gaps: List[Dict[str, str]] = []
        duplicate_count = 0
        anomalous_count = 0
        valid_candles: List[Candle] = []
        seen_timestamps: Set[datetime] = set()

        # Normalize column names to lowercase
        df = df.copy()
        df.columns = [str(c).strip().lower() for c in df.columns]

        timestamp_col = next((c for c in ["timestamp", "datetime", "date", "time"] if c in df.columns), None)
        if not timestamp_col:
            raise DataValidationError(f"Missing required timestamp column in {source_name}. Found: {list(df.columns)}")

        required_ohlc = ["open", "high", "low", "close"]
        for col in required_ohlc:
            if col not in df.columns:
                raise DataValidationError(f"Missing required price column '{col}' in {source_name}")

        has_volume = "volume" in df.columns

        # Iterate rows with rigorous validation
        for idx, row in df.iterrows():
            raw_ts = row[timestamp_col]
            # Parse timestamp to UTC
            try:
                if isinstance(raw_ts, (int, float)):
                    # Epoch timestamp (seconds or milliseconds)
                    epoch_val = float(raw_ts)
                    if epoch_val > 1e11:  # Milliseconds
                        epoch_val /= 1000.0
                    parsed_ts = datetime.fromtimestamp(epoch_val, tz=timezone.utc)
                else:
                    ts_str = str(raw_ts).strip()
                    parsed_ts = pd.to_datetime(ts_str).to_pydatetime()
                    if parsed_ts.tzinfo is None:
                        # Standard convention: if naive, assume IST market hours
                        parsed_ts = IST_TZ.localize(parsed_ts).astimezone(UTC_TZ)
                    else:
                        parsed_ts = parsed_ts.astimezone(UTC_TZ)
            except Exception as e:
                diagnostic_messages.append(f"Row {idx}: Invalid timestamp '{raw_ts}': {e}")
                anomalous_count += 1
                continue

            # Duplicate check
            if parsed_ts in seen_timestamps:
                duplicate_count += 1
                diagnostic_messages.append(f"Row {idx}: Duplicate timestamp {parsed_ts.isoformat()} rejected.")
                continue

            # Price validation
            try:
                op = Decimal(str(row["open"]))
                hi = Decimal(str(row["high"]))
                lo = Decimal(str(row["low"]))
                cl = Decimal(str(row["close"]))
                vol = int(row["volume"]) if has_volume and pd.notna(row["volume"]) else 0
            except (InvalidOperation, ValueError, TypeError) as e:
                diagnostic_messages.append(f"Row {idx} ({parsed_ts}): Malformed numeric price/volume: {e}")
                anomalous_count += 1
                continue

            # Boundary integrity checks
            if op <= 0 or hi <= 0 or lo <= 0 or cl <= 0 or vol < 0:
                diagnostic_messages.append(f"Row {idx} ({parsed_ts}): Non-positive price or negative volume.")
                anomalous_count += 1
                continue

            if hi < lo or hi < op or hi < cl or lo > op or lo > cl:
                diagnostic_messages.append(
                    f"Row {idx} ({parsed_ts}): Candle boundary violation (O={op}, H={hi}, L={lo}, C={cl})."
                )
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

        # Sort chronologically
        valid_candles.sort(key=lambda c: c.timestamp)

        # Gap detection across 1-minute intervals during exchange session (09:15 to 15:30 IST)
        gap_count = 0
        for i in range(1, len(valid_candles)):
            prev_ts = valid_candles[i - 1].timestamp
            curr_ts = valid_candles[i].timestamp
            delta = curr_ts - prev_ts
            # If within same trading day and gap > 1 minute
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

        # Build quality report
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
            is_usable_for_backtest=len(valid_candles) > 0 and anomalous_count == 0,
            diagnostic_messages=diagnostic_messages,
        )

        self._quality_reports[symbol] = report
        if is_option:
            self._option_candles[symbol] = valid_candles
        else:
            self._underlying_candles[symbol] = valid_candles

        return report

    def load_candles_from_csv(self, symbol: str, file_path: Path, is_option: bool = False) -> DataQualityReport:
        """Load and validate candles from CSV file."""
        if not file_path.exists():
            raise FileNotFoundError(f"Historical CSV file not found: {file_path}")
        df = pd.read_csv(file_path)
        return self.load_candles_from_dataframe(symbol, df, is_option=is_option, source_name=str(file_path))

    def load_candles_from_parquet(self, symbol: str, file_path: Path, is_option: bool = False) -> DataQualityReport:
        """Load and validate candles from Parquet file."""
        if not file_path.exists():
            raise FileNotFoundError(f"Historical Parquet file not found: {file_path}")
        df = pd.read_parquet(file_path)
        return self.load_candles_from_dataframe(symbol, df, is_option=is_option, source_name=str(file_path))

    def register_instrument(self, instrument: Instrument) -> None:
        """Register instrument metadata."""
        self._instruments[instrument.symbol] = instrument

    def register_option_contract(self, contract: OptionContract) -> None:
        """Register option contract derivative metadata."""
        self._option_contracts[contract.symbol] = contract

    # --------------------------------------------------------------------------
    # 2. MarketDataProvider Interface Implementation
    # --------------------------------------------------------------------------

    async def get_historical_candles(
        self,
        symbol: str,
        resolution: str,
        start_time: datetime,
        end_time: datetime,
    ) -> List[Candle]:
        """Fetch historical candles within [start_time, end_time] in UTC."""
        utc_start = to_utc(start_time)
        utc_end = to_utc(end_time)

        candles = self._underlying_candles.get(symbol) or self._option_candles.get(symbol) or []
        return [c for c in candles if utc_start <= c.timestamp <= utc_end]

    async def get_quote(self, symbol: str) -> MarketTick:
        """Get latest quote from the most recent historical candle."""
        candles = self._underlying_candles.get(symbol) or self._option_candles.get(symbol) or []
        if not candles:
            raise DataValidationError(f"No market data available for {symbol}")
        last_candle = candles[-1]
        return MarketTick(
            symbol=symbol,
            ltp=last_candle.close,
            volume=last_candle.volume,
            timestamp=last_candle.timestamp,
        )

    async def get_instrument(self, symbol: str) -> Optional[Instrument]:
        """Fetch instrument metadata."""
        return self._instruments.get(symbol)

    async def get_option_chain(
        self,
        underlying: str,
        expiry_date: date,
    ) -> List[OptionContract]:
        """List option contracts matching underlying and expiry."""
        return [
            c for c in self._option_contracts.values()
            if c.underlying == underlying and c.expiry_date == expiry_date
        ]

    async def get_available_expiries(self, underlying: str) -> List[date]:
        """List distinct expiration dates sorted chronologically."""
        expiries = {
            c.expiry_date for c in self._option_contracts.values()
            if c.underlying == underlying
        }
        return sorted(list(expiries))

    async def stream_ticks(self, symbols: List[str]) -> AsyncIterator[MarketTick]:
        """Stream simulated ticks derived from chronological candle closes."""
        all_candles: List[Candle] = []
        for sym in symbols:
            all_candles.extend(self._underlying_candles.get(sym, []))
            all_candles.extend(self._option_candles.get(sym, []))

        all_candles.sort(key=lambda c: c.timestamp)
        for candle in all_candles:
            yield MarketTick(
                symbol=candle.symbol,
                ltp=candle.close,
                volume=candle.volume,
                timestamp=candle.timestamp,
            )

    def get_source_metadata(self) -> Dict[str, Any]:
        """Provider identification and summary."""
        return {
            "provider_name": "OfflineHistoricalDataProvider",
            "underlying_symbols": list(self._underlying_candles.keys()),
            "option_symbols": list(self._option_candles.keys()),
            "quality_reports": {s: r.model_dump() for s, r in self._quality_reports.items()},
        }

    # --------------------------------------------------------------------------
    # 3. Deterministic Historical Replay for Multi-Asset Backtesting
    # --------------------------------------------------------------------------

    def replay_chronological(
        self,
        symbols: List[str],
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Candle]:
        """
        Return a deterministic chronological interleaved stream of candles
        across multiple symbols (e.g. NIFTY spot + active option contracts)
        strictly ordered by timestamp.
        """
        collected: List[Candle] = []
        utc_start = to_utc(start_time) if start_time else None
        utc_end = to_utc(end_time) if end_time else None

        for sym in symbols:
            candles = self._underlying_candles.get(sym) or self._option_candles.get(sym) or []
            for c in candles:
                if utc_start and c.timestamp < utc_start:
                    continue
                if utc_end and c.timestamp > utc_end:
                    continue
                collected.append(c)

        # Deterministic sort by timestamp, then symbol name for tie-breaking
        collected.sort(key=lambda c: (c.timestamp, c.symbol))
        return collected
