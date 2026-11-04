"""Deterministic tick-to-candle aggregation for live market data.

Builds fixed-interval candles from *exchange-timestamped* ticks. Pure logic: no
network, no clock reads, no I/O. All session boundaries are evaluated in IST.

Safety properties:
- A candle is returned (finalized) only once its interval has fully elapsed in
  exchange time (a tick for a later interval arrived, or ``finalize_elapsed`` was
  called with an exchange-time watermark past the interval end).
- Ticks for an already finalized interval are rejected (never retro-edit a candle
  the strategy may already have consumed).
- Exact duplicate ticks are dropped; ticks older than the newest accepted tick
  are dropped as out-of-order.
- Intervals with no ticks are never fabricated; they are counted as gaps.
- A partial candle is discarded (never published) if the feed is interrupted.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from src.common.time_utils import IST_TZ, to_ist, to_utc
from src.core.models import Candle, MarketTick


class TickCandleBuilder:
    """Aggregate ticks of a single symbol into finalized fixed-interval candles."""

    def __init__(
        self,
        symbol: str,
        interval_seconds: int = 60,
        session_open: time = time(9, 15, 0),
        session_close: time = time(15, 30, 0),
    ) -> None:
        if interval_seconds <= 0 or 86400 % interval_seconds != 0:
            raise ValueError("interval_seconds must be a positive divisor of 86400")
        self.symbol = symbol
        self.interval = timedelta(seconds=interval_seconds)
        self.session_open = session_open
        self.session_close = session_close

        self._bucket_start: Optional[datetime] = None  # UTC
        self._open: Optional[Decimal] = None
        self._high: Optional[Decimal] = None
        self._low: Optional[Decimal] = None
        self._close: Optional[Decimal] = None
        self._volume_first: Optional[int] = None
        self._volume_last: Optional[int] = None
        self._tick_count = 0

        self._last_tick_ts: Optional[datetime] = None
        self._last_tick_key: Optional[Tuple[datetime, Decimal, int]] = None
        self._last_finalized_start: Optional[datetime] = None

        self.stats: Dict[str, int] = {
            "ticks_accepted": 0,
            "duplicate_ticks": 0,
            "out_of_order_ticks": 0,
            "late_ticks": 0,
            "outside_session_ticks": 0,
            "wrong_symbol_ticks": 0,
            "candles_finalized": 0,
            "gap_intervals": 0,
            "partial_candles_discarded": 0,
        }

    # ------------------------------------------------------------------
    def _bucket_of(self, ts_utc: datetime) -> datetime:
        """Interval start (UTC) for an exchange timestamp, aligned to IST midnight."""
        ist = to_ist(ts_utc)
        midnight = ist.replace(hour=0, minute=0, second=0, microsecond=0)
        elapsed = (ist - midnight).total_seconds()
        step = self.interval.total_seconds()
        start_ist = midnight + timedelta(seconds=(elapsed // step) * step)
        return to_utc(start_ist)

    def _in_session(self, ts_utc: datetime) -> bool:
        ist_time = to_ist(ts_utc).time()
        return self.session_open <= ist_time < self.session_close

    @property
    def has_open_candle(self) -> bool:
        return self._bucket_start is not None

    @property
    def current_bucket_start(self) -> Optional[datetime]:
        return self._bucket_start

    # ------------------------------------------------------------------
    def on_tick(self, tick: MarketTick) -> List[Candle]:
        """Ingest one tick; return zero or one newly finalized candle(s)."""
        if tick.symbol != self.symbol:
            self.stats["wrong_symbol_ticks"] += 1
            return []

        ts = to_utc(tick.timestamp)
        if not self._in_session(ts):
            self.stats["outside_session_ticks"] += 1
            return []

        key = (ts, tick.ltp, tick.volume)
        if self._last_tick_key == key:
            self.stats["duplicate_ticks"] += 1
            return []
        if self._last_tick_ts is not None and ts < self._last_tick_ts:
            self.stats["out_of_order_ticks"] += 1
            return []

        bucket = self._bucket_of(ts)
        if self._last_finalized_start is not None and bucket <= self._last_finalized_start:
            self.stats["late_ticks"] += 1
            return []

        finalized: List[Candle] = []
        if self._bucket_start is not None and bucket > self._bucket_start:
            finalized.extend(self._finalize_current())

        if self._bucket_start is None:
            if self._last_finalized_start is not None:
                missing = int((bucket - self._last_finalized_start) / self.interval) - 1
                if missing > 0:
                    self.stats["gap_intervals"] += missing
            self._start_bucket(bucket, tick)
        else:
            price = tick.ltp
            self._high = max(self._high, price)  # type: ignore[type-var]
            self._low = min(self._low, price)  # type: ignore[type-var]
            self._close = price
            self._volume_last = tick.volume
            self._tick_count += 1

        self._last_tick_ts = ts
        self._last_tick_key = key
        self.stats["ticks_accepted"] += 1
        return finalized

    def finalize_elapsed(self, exchange_watermark: datetime) -> List[Candle]:
        """Finalize the open candle if the exchange clock has passed its interval end.

        ``exchange_watermark`` must be derived from exchange timestamps (e.g. the
        newest accepted tick time), never from a fabricated value.
        """
        if self._bucket_start is None:
            return []
        if to_utc(exchange_watermark) >= self._bucket_start + self.interval:
            return self._finalize_current()
        return []

    def discard_partial(self) -> bool:
        """Drop the in-progress candle (e.g. feed interrupted). Returns True if dropped."""
        if self._bucket_start is None:
            return False
        self.stats["partial_candles_discarded"] += 1
        discarded_bucket = self._bucket_start
        self._reset_bucket()
        # Ticks received after an interruption must not be merged with pre-gap data:
        # the discarded interval is closed to further ticks.
        self._last_finalized_start = discarded_bucket
        return True

    # ------------------------------------------------------------------
    def _start_bucket(self, bucket: datetime, tick: MarketTick) -> None:
        self._bucket_start = bucket
        self._open = self._high = self._low = self._close = tick.ltp
        self._volume_first = self._volume_last = tick.volume
        self._tick_count = 1

    def _reset_bucket(self) -> None:
        self._bucket_start = None
        self._open = self._high = self._low = self._close = None
        self._volume_first = self._volume_last = None
        self._tick_count = 0

    def _finalize_current(self) -> List[Candle]:
        assert self._bucket_start is not None
        volume = 0
        if self._volume_first is not None and self._volume_last is not None:
            volume = max(0, self._volume_last - self._volume_first)
        candle = Candle(
            symbol=self.symbol,
            timestamp=self._bucket_start,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            volume=volume,
        )
        self._last_finalized_start = self._bucket_start
        self._reset_bucket()
        self.stats["candles_finalized"] += 1
        return [candle]
