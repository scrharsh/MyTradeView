"""Unit tests for the FYERS WebSocket feed state machine using a FAKE socket.

These tests exercise our handling logic only. They are NOT evidence of live FYERS connectivity.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.common.exceptions import BrokerConnectionError
from src.data.fyers_adapter import FyersMarketDataAdapter
from src.data.fyers_live_feed import FyersLiveFeed

SYM = "NSE:NIFTY50-INDEX"
TOKEN = "tok_abcdef0123456789_VALID"


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 6, 4, 0, 0, tzinfo=timezone.utc)  # 09:30 IST

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += timedelta(seconds=s)


class FakeSocket:
    instances = []

    def __init__(self, access_token, on_message, on_error, on_connect, on_close):
        self.access_token = access_token
        self.on_message, self.on_error, self.on_connect, self.on_close = on_message, on_error, on_connect, on_close
        self.subscribed_to = []
        self.closed = False
        FakeSocket.instances.append(self)

    def connect(self):
        self.on_connect()

    def subscribe(self, symbols, data_type="SymbolUpdate"):
        self.subscribed_to.append((tuple(symbols), data_type))

    def close_connection(self):
        self.closed = True


def make_feed(clock, **kw):
    FakeSocket.instances = []
    return FyersLiveFeed(
        symbol=SYM,
        credentials_provider=lambda: ("APP-100", TOKEN),
        socket_factory=FakeSocket,
        now_fn=clock,
        **kw,
    )


def tick_msg(clock, price=25000.5, offset=0, **over):
    m = {"symbol": SYM, "ltp": price, "exch_feed_time": int(clock().timestamp()) + offset, "type": "if"}
    m.update(over)
    return m


async def test_connect_subscribe_ack_tick_sequence_reports_distinct_stages():
    clock = Clock()
    feed = make_feed(clock)
    await feed.start()
    sock = FakeSocket.instances[0]
    try:
        # token is passed in FYERS "client_id:token" format and is never exposed in the snapshot
        assert sock.access_token == f"APP-100:{TOKEN}"
        assert feed.ws_connected is True and feed.subscribed is False
        assert sock.subscribed_to == [((SYM,), "SymbolUpdate")]
        assert feed.connection_state == "CONNECTED" and feed.is_live is False

        sock.on_message({"type": "sub", "code": 200, "s": "ok", "message": "Subscribed successfully"})
        assert feed.subscribed is True
        assert feed.connection_state == "SUBSCRIBED_NO_FRESH_TICKS"
        assert feed.is_live is False  # subscription ack alone is not live data

        sock.on_message(tick_msg(clock))
        assert feed.is_live is True and feed.connection_state == "LIVE_STREAMING"
        assert TOKEN not in str(feed.snapshot())
    finally:
        await feed.stop()


async def test_tick_uses_exchange_timestamp_and_decimal_price():
    clock = Clock()
    feed = make_feed(clock)
    await feed.start()
    try:
        FakeSocket.instances[0].on_message(tick_msg(clock, price=25001.35, offset=-3))
        ev = await asyncio.wait_for(feed.events().__anext__(), 2)
        assert ev.kind == "tick"
        assert ev.tick.timestamp == datetime.fromtimestamp(int(clock().timestamp()) - 3, tz=timezone.utc)
        assert str(ev.tick.ltp) == "25001.35"
    finally:
        await feed.stop()


async def test_invalid_ticks_rejected_never_fabricated():
    clock = Clock()
    feed = make_feed(clock)
    await feed.start()
    s = FakeSocket.instances[0]
    try:
        s.on_message({"symbol": SYM, "ltp": 25000})                                   # no exchange timestamp
        s.on_message(tick_msg(clock, price=0))                                       # bad price
        s.on_message(tick_msg(clock, symbol="NSE:NIFTYBANK-INDEX"))                  # other symbol
        s.on_message(tick_msg(clock, offset=3600))                                   # exchange time far in the future
        assert feed.stats["ticks_received"] == 0
        assert feed.stats["ticks_rejected_no_timestamp"] == 1
        assert feed.stats["ticks_rejected_bad_price"] == 1
        assert feed.stats["ticks_ignored_other_symbol"] == 1
        assert feed.stats["ticks_rejected_future_timestamp"] == 1
        assert feed.is_live is False
    finally:
        await feed.stop()


async def test_stale_feed_detected_and_event_emitted():
    clock = Clock()
    feed = make_feed(clock, stale_after_seconds=30)
    await feed.start()
    s = FakeSocket.instances[0]
    try:
        s.on_message({"type": "sub", "code": 200, "s": "ok", "message": "Subscribed successfully"})
        s.on_message(tick_msg(clock))
        assert feed.is_live
        clock.advance(31)
        assert feed.tick_fresh is False and feed.is_live is False
        assert feed.connection_state == "STALE"
        await asyncio.sleep(1.3)  # supervisor emits a one-time stale event
        kinds = []
        while not feed._queue.empty():
            kinds.append(feed._queue.get_nowait().kind)
        assert "stale" in kinds
    finally:
        await feed.stop()


async def test_disconnect_then_automatic_reconnect_and_resubscribe():
    clock = Clock()
    feed = make_feed(clock, reconnect_after_seconds=0.5)
    await feed.start()
    first = FakeSocket.instances[0]
    try:
        first.on_message({"type": "sub", "code": 200, "s": "ok", "message": "Subscribed successfully"})
        first.on_message(tick_msg(clock))
        assert feed.is_live
        first.on_close()
        assert feed.ws_connected is False and feed.subscribed is False and feed.is_live is False
        assert feed.connection_state == "RECONNECTING"
        clock.advance(1)
        await asyncio.sleep(2.3)  # supervisor loop notices and reconnects
        assert len(FakeSocket.instances) >= 2
        assert first.closed is True
        second = FakeSocket.instances[-1]
        assert second.subscribed_to == [((SYM,), "SymbolUpdate")]  # re-subscribed
        assert feed.reconnect_count >= 1 and feed.ws_connected is True
        assert feed.is_live is False  # connected, but no fresh post-reconnect... until a new tick
        clock.advance(1)
        second.on_message({"type": "sub", "code": 200, "s": "ok", "message": "Subscribed successfully"})
        second.on_message(tick_msg(clock))
        assert feed.is_live is True
    finally:
        await feed.stop()


async def test_disconnect_event_emitted_for_candle_discard():
    clock = Clock()
    feed = make_feed(clock)
    await feed.start()
    try:
        FakeSocket.instances[0].on_error("network reset")
        ev = await asyncio.wait_for(feed.events().__anext__(), 2)
        assert ev.kind == "disconnect"
    finally:
        await feed.stop()


async def test_auth_rejection_fails_closed_without_reconnect_loop():
    clock = Clock()
    feed = make_feed(clock, reconnect_after_seconds=0.2)
    await feed.start()
    s = FakeSocket.instances[0]
    try:
        s.on_message({"s": "error", "code": -300, "message": f"Invalid token {TOKEN}"})
        assert feed.auth_failed is True and feed.is_live is False
        assert feed.connection_state == "AUTH_FAILED"
        assert TOKEN not in feed.last_error
        await asyncio.sleep(1.5)
        assert len(FakeSocket.instances) == 1  # no reconnect with a rejected token
    finally:
        await feed.stop()


async def test_start_without_credentials_raises():
    feed = FyersLiveFeed(symbol=SYM, credentials_provider=lambda: None, socket_factory=FakeSocket)
    with pytest.raises(BrokerConnectionError):
        await feed.start()


async def test_adapter_stream_ticks_requires_credentials():
    adapter = FyersMarketDataAdapter()
    with pytest.raises(BrokerConnectionError):
        async for _ in adapter.stream_ticks([SYM]):
            pass


async def test_adapter_stream_ticks_yields_ticks_from_socket():
    FakeSocket.instances = []
    adapter = FyersMarketDataAdapter(credentials_provider=lambda: ("APP-100", TOKEN), socket_factory=FakeSocket)
    gen = adapter.stream_ticks([SYM])
    now = int(datetime.now(timezone.utc).timestamp())

    async def pump():
        for _ in range(50):
            await asyncio.sleep(0.05)
            if FakeSocket.instances:
                FakeSocket.instances[0].on_message({"symbol": SYM, "ltp": 25010.0, "exch_feed_time": now})
                return

    task = asyncio.create_task(pump())
    tick = await asyncio.wait_for(gen.__anext__(), 5)
    await task
    assert tick.symbol == SYM and str(tick.ltp) == "25010.0"
    await gen.aclose()
