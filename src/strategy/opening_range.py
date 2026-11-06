"""Opening Range Builder and Breakout State Tracker.

Computes the NIFTY Opening Range (OR_HIGH and OR_LOW) between 09:16:00 IST
and 09:45:00 IST with strict boundary semantics and deterministic freezing.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional, Set

from src.common.constants import RangeBoundaryMode
from src.common.time_utils import IST_TZ, to_ist, to_utc
from src.core.models import Candle, MarketTick, OpeningRange


class OpeningRangeTracker:
    """
    Manages opening range formation and breakout detection.
    
    Supports both standard StockMock interval semantics:
        - UP_TO_0944_CLOSE: Legacy mode; 29 bars from 09:16:00 through 09:44:59 build the range.
        - UP_TO_0945_CLOSE: Active mode; 30 bars from 09:16:00 through 09:45:59 build the range.
            The 09:46:00 candle is the first breakout evaluation candle.
    """

    def __init__(
        self,
        symbol: str = "NSE:NIFTY50-INDEX",
        start_time: time = time(9, 16, 0),
        end_time: time = time(9, 45, 0),
        boundary_mode: RangeBoundaryMode = RangeBoundaryMode.UP_TO_0945_CLOSE,
    ) -> None:
        self.symbol = symbol
        self.start_time = start_time
        self.end_time = end_time
        self.boundary_mode = boundary_mode

        self._session_date: Optional[date] = None
        self._high: Optional[Decimal] = None
        self._low: Optional[Decimal] = None
        self._is_established: bool = False
        self._candle_count: int = 0
        self._finalized_at: Optional[datetime] = None
        self._formation_timestamps: Set[datetime] = set()

    @property
    def expected_candle_count(self) -> int:
        """Number of completed one-minute candles required before freezing the range."""
        return 30 if self.boundary_mode == RangeBoundaryMode.UP_TO_0945_CLOSE else 29

    def reset(self, session_date: date) -> None:
        """Reset the opening range state for a new trading session."""
        self._session_date = session_date
        self._high = None
        self._low = None
        self._is_established = False
        self._candle_count = 0
        self._finalized_at = None
        self._formation_timestamps.clear()

    def _expected_formation_timestamps(self) -> Set[datetime]:
        """Return the expected UTC start timestamps for the active range window."""
        if self._session_date is None:
            return set()
        end_time = self.end_time
        if self.boundary_mode == RangeBoundaryMode.UP_TO_0944_CLOSE:
            end_time = (datetime.combine(self._session_date, end_time) - timedelta(minutes=1)).time()
        current = IST_TZ.localize(datetime.combine(self._session_date, self.start_time))
        end = IST_TZ.localize(datetime.combine(self._session_date, end_time))
        expected: Set[datetime] = set()
        while current <= end:
            expected.add(to_utc(current))
            current += timedelta(minutes=1)
        return expected

    def _active_range_complete(self) -> bool:
        return self._formation_timestamps == self._expected_formation_timestamps()

    @property
    def is_established(self) -> bool:
        """Whether the range formation window has concluded and boundaries are frozen."""
        return self._is_established

    @property
    def high(self) -> Optional[Decimal]:
        """Established Opening Range High in Decimal."""
        return self._high

    @property
    def low(self) -> Optional[Decimal]:
        """Established Opening Range Low in Decimal."""
        return self._low

    @property
    def range_width(self) -> Optional[Decimal]:
        """Established range width (High - Low) in points."""
        if self._high is not None and self._low is not None:
            return self._high - self._low
        return None

    @property
    def candle_count(self) -> int:
        """Total number of bars ingested during the range formation window."""
        return self._candle_count

    def to_domain_model(self) -> OpeningRange:
        """Convert tracker state to the immutable domain model."""
        return OpeningRange(
            symbol=self.symbol,
            session_date=self._session_date or date.today(),
            start_time=self.start_time,
            end_time=self.end_time,
            high=self._high,
            low=self._low,
            is_established=self._is_established,
            candle_count=self._candle_count,
        )

    def is_candle_in_range_window(self, candle_time_ist: time) -> bool:
        """
        Check if a 1-minute candle's start time belongs inside the range formation window.
        
        For UP_TO_0944_CLOSE:
            09:16:00 <= candle_start < 09:45:00
        For UP_TO_0945_CLOSE:
            09:16:00 <= candle_start <= 09:45:00
        """
        if candle_time_ist < self.start_time:
            return False

        if self.boundary_mode == RangeBoundaryMode.UP_TO_0944_CLOSE:
            return candle_time_ist < self.end_time
        return candle_time_ist <= self.end_time

    def is_eligible_for_finalization(self, current_time_ist: time) -> bool:
        """
        Check if the current time warrants freezing the opening range.
        
        For UP_TO_0944_CLOSE:
            Finalizes at or after 09:45:00 IST.
        For UP_TO_0945_CLOSE:
            Finalizes at or after 09:46:00 IST.
        """
        if self.boundary_mode == RangeBoundaryMode.UP_TO_0944_CLOSE:
            return current_time_ist >= self.end_time
        # 09:45 bar closes at 09:46:00.
        cutoff = time(self.end_time.hour, self.end_time.minute + 1, self.end_time.second)
        return current_time_ist >= cutoff

    def update_from_candle(self, candle: Candle) -> bool:
        """
        Ingest a 1-minute candle.
        
        Returns:
            True if the candle caused the range to be finalized, False otherwise.
        """
        if candle.symbol != self.symbol:
            return False

        candle_ist = to_ist(candle.timestamp)
        bar_time = candle_ist.time()

        # If already established, do not alter boundaries
        if self._is_established:
            return False

        # If this candle is at or past the finalization boundary, freeze now
        if self.is_eligible_for_finalization(bar_time):
            if self.boundary_mode == RangeBoundaryMode.UP_TO_0945_CLOSE and not self._active_range_complete():
                return False
            self.finalize(candle.timestamp)
            return True

        # Check if candle is inside the formation window
        if self.is_candle_in_range_window(bar_time):
            if self.boundary_mode == RangeBoundaryMode.UP_TO_0945_CLOSE:
                timestamp = to_utc(candle.timestamp)
                expected = self._expected_formation_timestamps()
                if timestamp not in expected or timestamp in self._formation_timestamps:
                    return False
                self._formation_timestamps.add(timestamp)
                self._candle_count = len(self._formation_timestamps)
            else:
                self._candle_count += 1
            if self._high is None or candle.high > self._high:
                self._high = candle.high
            if self._low is None or candle.low < self._low:
                self._low = candle.low

        return False

    def update_from_tick(self, tick: MarketTick) -> bool:
        """
        Ingest a live market tick during range formation.
        
        Returns:
            True if the tick caused the range to be finalized, False otherwise.
        """
        if tick.symbol != self.symbol:
            return False

        tick_ist = to_ist(tick.timestamp)
        tick_time = tick_ist.time()

        if self._is_established:
            return False

        if self.is_eligible_for_finalization(tick_time):
            if self.boundary_mode == RangeBoundaryMode.UP_TO_0945_CLOSE and not self._active_range_complete():
                return False
            self.finalize(tick.timestamp)
            return True

        active_tick_cutoff = (datetime.combine(date.today(), self.end_time) + timedelta(minutes=1)).time()
        in_tick_window = (
            self.start_time <= tick_time < active_tick_cutoff
            if self.boundary_mode == RangeBoundaryMode.UP_TO_0945_CLOSE
            else self.start_time <= tick_time < self.end_time
        )
        if in_tick_window:
            if self.boundary_mode == RangeBoundaryMode.UP_TO_0945_CLOSE:
                timestamp = to_utc(tick.timestamp).replace(second=0, microsecond=0)
                expected = self._expected_formation_timestamps()
                if timestamp not in expected:
                    return False
                is_new_minute = timestamp not in self._formation_timestamps
                self._formation_timestamps.add(timestamp)
                self._candle_count = len(self._formation_timestamps)
            else:
                is_new_minute = True
            if self._high is None or tick.ltp > self._high:
                self._high = tick.ltp
            if self._low is None or tick.ltp < self._low:
                self._low = tick.ltp
            if not is_new_minute:
                return False

        return False

    def finalize(self, timestamp: Optional[datetime] = None) -> None:
        """Freeze range boundaries and mark range as established."""
        if not self._is_established:
            if self._high is not None and self._low is not None:
                self._is_established = True
                self._finalized_at = timestamp

    def is_broken_above(self, price: Decimal) -> bool:
        """Check if price strictly breaches the established range high."""
        if not self._is_established or self._high is None:
            return False
        return price > self._high

    def is_broken_below(self, price: Decimal) -> bool:
        """Check if price strictly breaches the established range low."""
        if not self._is_established or self._low is None:
            return False
        return price < self._low
