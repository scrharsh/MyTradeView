"""Timezone utilities and exchange session calendar helpers."""

from datetime import datetime, time, timezone
from typing import Sequence
import pytz

IST_TZ = pytz.timezone("Asia/Kolkata")
UTC_TZ = timezone.utc


def now_utc() -> datetime:
    """Return the current time in UTC with timezone awareness."""
    return datetime.now(UTC_TZ)


def now_ist() -> datetime:
    """Return the current time in Indian Standard Time (IST) with timezone awareness."""
    return datetime.now(IST_TZ)


def to_ist(dt: datetime) -> datetime:
    """Convert an arbitrary datetime to Asia/Kolkata timezone."""
    if dt.tzinfo is None:
        # Assume naive datetime is in UTC
        dt = dt.replace(tzinfo=UTC_TZ)
    return dt.astimezone(IST_TZ)


def to_utc(dt: datetime) -> datetime:
    """Convert an arbitrary datetime to UTC timezone."""
    if dt.tzinfo is None:
        # Assume naive datetime is already in IST
        dt = IST_TZ.localize(dt)
    return dt.astimezone(UTC_TZ)


def is_trading_day(dt: datetime, allowed_days: Sequence[str]) -> bool:
    """Check if the provided datetime falls on an allowed trading day (e.g. Monday, Thursday, Friday)."""
    ist_dt = to_ist(dt)
    day_name = ist_dt.strftime("%A")
    return day_name in allowed_days


def is_within_time_window(current_time: time, start_time: time, end_time: time) -> bool:
    """Check if a time falls within [start_time, end_time] inclusive."""
    return start_time <= current_time <= end_time


def format_iso_ist(dt: datetime) -> str:
    """Format datetime as ISO string in IST timezone."""
    return to_ist(dt).isoformat()

