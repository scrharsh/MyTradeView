"""Integration tests of the live pipeline using MOCKED FYERS responses and a FAKE WebSocket.

IMPORTANT: These verify our wiring (auth -> feed -> candles -> paper strategy/OMS -> status/API).
They are NOT genuine FYERS connectivity and must never be reported as such.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.service import WorkstationService
from src.broker.fyers_auth import FyersAuthError, FyersAuthManager
from src.common.time_utils import IST_TZ, to_utc
from tests.unit.test_fyers_live_feed import FakeSocket, SYM, TOKEN


class FakeModel:
    def __init__(self, resp):
        self.resp = resp

    def get_profile(self):
        return self.resp


@pytest.fixture
async def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("FYERS_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'live_test.db'}")
    s = WorkstationService()
    await s.initialize()
    s.fyers_auth = FyersAuthManager(
        client_id="APP-100",
        secret_key="SECRET-123456",
        redirect_uri="https://127.0.0.1:5000/login",
        token_cache_path=tmp_path / "secrets" / "fyers_token.json",
        model_factory=lambda cid, tok, lp: FakeModel({"s": "ok", "code": 200}),
        log_dir=tmp_path / "logs",
    )
    s.live_socket_factory = FakeSocket
    FakeSocket.instances = []
    yield s
    await s.stop_live_feed()


async def test_cannot_start_live_feed_without_validated_auth(svc):
    with pytest.raises(FyersAuthError):
        await svc.start_live_feed()
    assert svc.live_pipeline is None and svc.live_data_connected is False
    h = await svc.get_system_health()
    assert h.fyers_authenticated is False and h.data_source != "LIVE"


async def test_token_presence_without_validation_is_not_authenticated(svc):
    svc.fyers_auth.save_token(TOKEN)
    h = await svc.get_system_health()
    assert h.fyers_authenticated is False
    assert svc.get_market_data_status()["feed_connected"] is False


async def test_end_to_end_mocked_pipeline_builds_live_candles_and_separates_data(svc):
    svc.fyers_auth.save_token(TOKEN)
    status = await svc.start_live_feed()  # validates via mocked profile, opens FAKE socket
    assert status["authentication"]["authenticated"] is True
    assert status["websocket_connected"] is True and status["subscribed"] is False
    assert status["live_data_connected"] is False  # no ack, no tick yet

    sock = FakeSocket.instances[0]
    sock.on_message({"type": "sub", "code": 200, "s": "ok", "message": "Subscribed"})
    status = svc.get_live_feed_status()
    assert status["subscribed"] is True and status["fresh_tick_received"] is False
    assert svc.live_data_connected is False and svc.get_market_data_status()["data_source"] != "LIVE"

    # Exchange timestamps from yesterday 10:00 IST (always in the past, inside the session window).
    t0 = to_utc(datetime.now(IST_TZ).replace(hour=10, minute=0, second=0, microsecond=0) - timedelta(days=1))
    for i, price in enumerate([25000.0, 25010.0, 24995.0, 25005.0]):
        sock.on_message({"symbol": SYM, "ltp": price, "exch_feed_time": int((t0 + timedelta(seconds=10 * i)).timestamp())})
    sock.on_message({"symbol": SYM, "ltp": 25006.0, "exch_feed_time": int((t0 + timedelta(minutes=1)).timestamp())})
    await asyncio.sleep(0.3)

    candles = svc.live_candles.get(SYM, [])
    assert len(candles) == 1
    c = candles[0]
    assert (float(c.open), float(c.high), float(c.low), float(c.close)) == (25000.0, 25010.0, 24995.0, 25005.0)
    assert c.timestamp == t0
    # live and synthetic caches are separate objects with no cross-contamination
    assert all(x.timestamp != t0 or x not in svc.sample_candles.get(SYM, []) for x in candles)

    # Dashboard/API reflect live state truthfully
    st = svc.get_market_data_status()
    assert st["fyers_authenticated"] is True
    assert st["websocket_connected"] is True and st["subscribed"] is True
    assert st["execution_mode"] == "paper"
    resp = svc.get_candles(SYM)
    assert resp.data_source == "LIVE" and resp.is_synthetic is False and resp.candle_count == 1

    # No orders or positions were created by market data alone / no broker order submitted
    assert svc.list_orders() == [] and svc.list_positions() == []


async def test_disconnect_discards_partial_candle_and_fails_closed(svc):
    svc.fyers_auth.save_token(TOKEN)
    await svc.start_live_feed()
    sock = FakeSocket.instances[0]
    t0 = to_utc(datetime.now(IST_TZ).replace(hour=10, minute=0, second=0, microsecond=0) - timedelta(days=1))
    sock.on_message({"symbol": SYM, "ltp": 25000.0, "exch_feed_time": int(t0.timestamp())})
    await asyncio.sleep(0.1)
    assert svc.live_pipeline.builder.has_open_candle
    sock.on_close()
    await asyncio.sleep(0.3)
    assert not svc.live_pipeline.builder.has_open_candle
    assert svc.live_pipeline.builder.stats["partial_candles_discarded"] == 1
    assert svc.live_candles.get(SYM, []) == []  # partial bar never published
    assert svc.live_data_connected is False


async def test_auth_rejected_by_feed_invalidates_session(svc):
    svc.fyers_auth.save_token(TOKEN)
    await svc.start_live_feed()
    FakeSocket.instances[0].on_message({"s": "error", "code": -300, "message": "Invalid token"})
    await asyncio.sleep(0.3)
    assert svc.fyers_auth.is_authenticated is False
    assert svc.live_data_connected is False


def test_api_status_endpoints_expose_no_secrets():
    with TestClient(app) as client:
        for path in ("/api/v1/auth/fyers/status", "/api/v1/market-data/live/status", "/api/v1/market-data/status", "/api/v1/health"):
            body = client.get(path).text
            assert "access_token" not in body.lower() or '"access_token"' not in body
            assert TOKEN not in body
        r = client.post("/api/v1/market-data/live/start")
        assert r.status_code in (409, 502)  # fails closed when unauthenticated
        assert client.get("/api/v1/health").json()["execution_mode"] == "paper"
