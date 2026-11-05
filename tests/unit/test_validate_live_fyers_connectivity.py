"""Regression tests for truthful live-connectivity validation."""

from datetime import datetime, timedelta, timezone

import pytest

from scripts.validate_live_fyers_connectivity import LiveTickMetrics, consume_tick, live_ticks_status
from src.core.models import MarketTick
from src.data.candle_builder import TickCandleBuilder
from src.data.fyers_live_feed import FyersLiveFeed

SYMBOL = "NSE:NIFTY50-INDEX"


def make_tick(timestamp: datetime, symbol: str = SYMBOL, price: str = "25000.00") -> MarketTick:
    return MarketTick(symbol=symbol, ltp=price, volume=100, timestamp=timestamp)


def test_after_hours_tick_is_not_live_tick_pass():
    builder = TickCandleBuilder(SYMBOL)
    metrics = LiveTickMetrics()

    consume_tick(builder, make_tick(datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)), metrics)

    assert metrics.raw_ticks_received == 1
    assert metrics.exchange_timestamps_valid == 1
    assert metrics.ticks_accepted_by_builder == 0
    assert builder.stats["outside_session_ticks"] == 1
    assert live_ticks_status(metrics, market_open=False) == "OUTSIDE_SESSION"


def test_in_session_tick_is_accepted():
    builder = TickCandleBuilder(SYMBOL)
    metrics = LiveTickMetrics()

    consume_tick(builder, make_tick(datetime(2026, 11, 6, 4, 0, tzinfo=timezone.utc)), metrics)

    assert metrics.raw_ticks_received == 1
    assert metrics.exchange_timestamps_valid == 1
    assert metrics.ticks_accepted_by_builder == 1
    assert live_ticks_status(metrics, market_open=True) == "PASS"


def test_raw_tick_rejected_by_candle_builder_does_not_pass_live_ticks():
    builder = TickCandleBuilder(SYMBOL)
    metrics = LiveTickMetrics()

    consume_tick(
        builder,
        make_tick(datetime(2026, 11, 6, 4, 0, tzinfo=timezone.utc), symbol="NSE:NIFTYBANK-INDEX"),
        metrics,
    )

    assert metrics.raw_ticks_received == 1
    assert metrics.exchange_timestamps_valid == 1
    assert metrics.ticks_accepted_by_builder == 0
    assert builder.stats["wrong_symbol_ticks"] == 1
    assert live_ticks_status(metrics, market_open=True) == "FAIL"


class Clock:
    def __init__(self):
        self.current = datetime(2026, 11, 6, 4, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.current


class FakeSocket:
    def __init__(self, access_token, on_message, on_error, on_connect, on_close):
        self.on_connect = on_connect
        self.on_close = on_close
        self.on_message = on_message

    def connect(self):
        self.on_connect()

    def subscribe(self, symbols, data_type="SymbolUpdate"):
        pass

    def close_connection(self):
        pass


def make_feed(clock: Clock) -> FyersLiveFeed:
    return FyersLiveFeed(
        symbol=SYMBOL,
        credentials_provider=lambda: ("APP-100", "TOKEN"),
        socket_factory=FakeSocket,
        stale_after_seconds=30,
        now_fn=clock,
    )


@pytest.mark.asyncio
async def test_connected_socket_with_stale_feed_is_not_live_streaming():
    clock = Clock()
    feed = make_feed(clock)
    await feed.start()
    try:
        socket = feed._socket
        socket.on_message({"type": "sub", "code": 200, "s": "ok", "message": "Subscribed"})
        socket.on_message({"symbol": SYMBOL, "ltp": 25000, "exch_feed_time": int(clock().timestamp())})
        clock.current += timedelta(seconds=31)

        assert feed.ws_connected is True
        assert feed.connection_state == "STALE"
        assert feed.is_live is False
    finally:
        await feed.stop()


@pytest.mark.asyncio
async def test_subscription_acknowledgement_without_ticks_is_distinct():
    clock = Clock()
    feed = make_feed(clock)
    await feed.start()
    try:
        feed._socket.on_message({"type": "sub", "code": 200, "s": "ok", "message": "Subscribed"})

        assert feed.subscribed is True
        assert feed.connection_state == "SUBSCRIBED_NO_FRESH_TICKS"
        assert feed.snapshot()["stats"]["raw_ticks_received"] == 0
        assert feed.is_live is False
    finally:
        await feed.stop()


def test_valid_ticks_finalize_one_minute_candle():
    builder = TickCandleBuilder(SYMBOL)
    metrics = LiveTickMetrics()
    first = datetime(2026, 11, 6, 4, 0, 10, tzinfo=timezone.utc)

    consume_tick(builder, make_tick(first), metrics)
    consume_tick(builder, make_tick(first + timedelta(minutes=1)), metrics)

    assert metrics.ticks_accepted_by_builder == 2
    assert metrics.candles_finalized == 1
    assert len(metrics.candles) == 1