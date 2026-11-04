"""Unit tests for tick-to-candle aggregation (pure logic; no network, no mocks of FYERS)."""

from datetime import datetime, timedelta
from decimal import Decimal

from src.common.time_utils import IST_TZ, to_utc
from src.core.models import MarketTick
from src.data.candle_builder import TickCandleBuilder

SYM = "NSE:NIFTY50-INDEX"


def ist(h, m, s=0, day=6):
    return to_utc(IST_TZ.localize(datetime(2026, 11, day, h, m, s)))


def tick(ts, price, vol=0, symbol=SYM):
    return MarketTick(symbol=symbol, ltp=Decimal(str(price)), volume=vol, timestamp=ts)


def feed(b, items):
    out = []
    for it in items:
        out.extend(b.on_tick(it))
    return out


def test_builds_correct_ohlc_with_ist_aligned_timestamp():
    b = TickCandleBuilder(SYM)
    out = feed(b, [
        tick(ist(9, 16, 1), 25000),
        tick(ist(9, 16, 20), 25010),
        tick(ist(9, 16, 40), 24995),
        tick(ist(9, 16, 59), 25005),
        tick(ist(9, 17, 0), 25006),  # next interval -> finalizes 09:16
    ])
    assert len(out) == 1
    c = out[0]
    assert (c.open, c.high, c.low, c.close) == (Decimal("25000"), Decimal("25010"), Decimal("24995"), Decimal("25005"))
    assert c.timestamp == ist(9, 16)  # bar start, aligned to IST minute


def test_incomplete_candle_is_never_finalized():
    b = TickCandleBuilder(SYM)
    out = feed(b, [tick(ist(9, 20, 5), 25000), tick(ist(9, 20, 30), 25001)])
    assert out == []
    assert b.has_open_candle
    # exchange clock has not passed interval end
    assert b.finalize_elapsed(ist(9, 20, 59)) == []
    assert len(b.finalize_elapsed(ist(9, 21, 0))) == 1


def test_duplicate_tick_dropped():
    b = TickCandleBuilder(SYM)
    feed(b, [tick(ist(9, 20, 5), 25000, 10), tick(ist(9, 20, 5), 25000, 10)])
    assert b.stats["duplicate_ticks"] == 1
    assert b.stats["ticks_accepted"] == 1


def test_out_of_order_tick_dropped_within_interval():
    b = TickCandleBuilder(SYM)
    feed(b, [tick(ist(9, 20, 30), 25000), tick(ist(9, 20, 10), 99999)])
    assert b.stats["out_of_order_ticks"] == 1
    out = b.finalize_elapsed(ist(9, 21, 0))
    assert out[0].high == Decimal("25000")  # the late tick did not pollute the bar


def test_late_tick_for_finalized_interval_rejected():
    b = TickCandleBuilder(SYM)
    out = feed(b, [tick(ist(9, 20, 30), 25000), tick(ist(9, 21, 1), 25010)])
    assert len(out) == 1
    # delayed tick from the already-finalized 09:20 interval (newer than nothing? older than last tick)
    assert b.on_tick(tick(ist(9, 20, 59), 30000)) == []
    assert b.stats["out_of_order_ticks"] + b.stats["late_ticks"] == 1


def test_missing_interval_counted_not_fabricated():
    b = TickCandleBuilder(SYM)
    out = feed(b, [
        tick(ist(9, 20, 5), 25000),
        tick(ist(9, 21, 5), 25001),   # finalizes 09:20
        tick(ist(9, 24, 5), 25002),   # finalizes 09:21; 09:22 and 09:23 had no ticks
    ])
    assert [c.timestamp for c in out] == [ist(9, 20), ist(9, 21)]
    assert b.stats["gap_intervals"] == 2  # 09:22 and 09:23 had no ticks
    out2 = feed(b, [tick(ist(9, 25, 5), 25003)])
    assert [c.timestamp for c in out2] == [ist(9, 24)]
    # No candles exist for 09:22/09:23
    assert ist(9, 22) not in [c.timestamp for c in out + out2]


def test_gap_intervals_counted_after_discard_and_resume():
    b = TickCandleBuilder(SYM)
    feed(b, [tick(ist(9, 20, 5), 25000)])
    assert b.discard_partial() is True
    feed(b, [tick(ist(9, 24, 5), 25010)])
    assert b.stats["gap_intervals"] == 3  # 09:21, 09:22, 09:23


def test_discarded_partial_interval_is_closed_to_further_ticks():
    b = TickCandleBuilder(SYM)
    feed(b, [tick(ist(9, 20, 5), 25000)])
    b.discard_partial()
    assert b.on_tick(tick(ist(9, 20, 50), 25100)) == []  # post-reconnect tick must not form a partial 09:20 bar
    assert b.stats["late_ticks"] == 1
    assert b.stats["partial_candles_discarded"] == 1


def test_ticks_outside_session_rejected():
    b = TickCandleBuilder(SYM)
    assert b.on_tick(tick(ist(9, 14, 59), 25000)) == []
    assert b.on_tick(tick(ist(15, 30, 0), 25000)) == []
    assert b.stats["outside_session_ticks"] == 2
    assert not b.has_open_candle


def test_wrong_symbol_rejected():
    b = TickCandleBuilder(SYM)
    b.on_tick(tick(ist(9, 20, 5), 25000, symbol="NSE:NIFTYBANK-INDEX"))
    assert b.stats["wrong_symbol_ticks"] == 1


def test_utc_exchange_timestamp_maps_to_ist_boundary():
    # 03:46:30 UTC == 09:16:30 IST -> belongs to 09:16 bar
    b = TickCandleBuilder(SYM)
    from datetime import timezone
    t = datetime(2026, 11, 6, 3, 46, 30, tzinfo=timezone.utc)
    b.on_tick(tick(t, 25000))
    assert b.current_bucket_start == ist(9, 16)


def test_five_minute_interval_supported():
    b = TickCandleBuilder(SYM, interval_seconds=300)
    out = feed(b, [tick(ist(9, 17, 0), 25000), tick(ist(9, 19, 59), 25010), tick(ist(9, 20, 0), 25020)])
    assert len(out) == 1 and out[0].timestamp == ist(9, 15)
