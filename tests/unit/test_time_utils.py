"""Unit tests for timezone conversion, IST market hours, and session boundaries."""

from datetime import datetime, time, timezone
import pytest
import pytz

from src.common.time_utils import (
    IST_TZ,
    UTC_TZ,
    format_iso_ist,
    is_trading_day,
    is_within_time_window,
    now_ist,
    now_utc,
    to_ist,
    to_utc,
)


def test_timezone_conversion_utc_to_ist():
    """Verify UTC to Asia/Kolkata (+05:30) exact conversion."""
    # 03:46:00 UTC = 09:16:00 IST
    utc_dt = datetime(2026, 11, 2, 3, 46, 0, tzinfo=UTC_TZ)
    ist_dt = to_ist(utc_dt)

    assert ist_dt.hour == 9
    assert ist_dt.minute == 16
    assert ist_dt.tzinfo.zone == "Asia/Kolkata"


def test_timezone_conversion_naive_handling():
    """Verify conversion assumes UTC when naive datetime is passed to to_ist."""
    naive_dt = datetime(2026, 11, 2, 3, 46, 0)
    ist_dt = to_ist(naive_dt)

    assert ist_dt.hour == 9
    assert ist_dt.minute == 16
    assert ist_dt.tzinfo.zone == "Asia/Kolkata"


def test_is_trading_day_filter():
    """Verify allowed trading days filter (Monday, Thursday, Friday)."""
    allowed = ["Monday", "Thursday", "Friday"]

    # 2026-11-02 is a Monday
    monday_dt = datetime(2026, 11, 2, 9, 30, 0, tzinfo=IST_TZ)
    assert is_trading_day(monday_dt, allowed) is True

    # 2026-11-03 is a Tuesday
    tuesday_dt = datetime(2026, 11, 3, 9, 30, 0, tzinfo=IST_TZ)
    assert is_trading_day(tuesday_dt, allowed) is False

    # 2026-11-04 is a Wednesday
    wednesday_dt = datetime(2026, 11, 4, 9, 30, 0, tzinfo=IST_TZ)
    assert is_trading_day(wednesday_dt, allowed) is False

    # 2026-11-05 is a Thursday
    thursday_dt = datetime(2026, 11, 5, 9, 30, 0, tzinfo=IST_TZ)
    assert is_trading_day(thursday_dt, allowed) is True

    # 2026-11-06 is a Friday
    friday_dt = datetime(2026, 11, 6, 9, 30, 0, tzinfo=IST_TZ)
    assert is_trading_day(friday_dt, allowed) is True


def test_is_within_time_window():
    """Verify time window containment checking."""
    start = time(9, 16, 0)
    end = time(9, 45, 0)

    assert is_within_time_window(time(9, 16, 0), start, end) is True
    assert is_within_time_window(time(9, 30, 0), start, end) is True
    assert is_within_time_window(time(9, 45, 0), start, end) is True
    assert is_within_time_window(time(9, 15, 59), start, end) is False
    assert is_within_time_window(time(9, 45, 1), start, end) is False


def test_format_iso_ist():
    """Verify ISO string formatting in IST timezone."""
    utc_dt = datetime(2026, 11, 2, 3, 46, 0, tzinfo=UTC_TZ)
    iso_str = format_iso_ist(utc_dt)
    assert "+05:30" in iso_str
    assert "09:16:00" in iso_str

