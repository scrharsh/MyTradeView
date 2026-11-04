"""Unit tests for FastAPI REST endpoints and WebSocket telemetry."""

import asyncio
from datetime import date
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.service import workstation_service


@pytest.fixture(scope="module", autouse=True)
def init_service():
    """Ensure workstation service is initialized before running tests."""
    with TestClient(app) as client:
        yield client


def test_health_endpoint(init_service):
    client = init_service
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "HEALTHY"
    assert data["execution_mode"] == "paper"
    assert data["broker_connected"] is True
    assert data["is_reconciled"] is True


def test_strategy_templates(init_service):
    client = init_service
    resp = client.get("/api/v1/strategies/templates")
    assert resp.status_code == 200
    templates = resp.json()
    assert len(templates) >= 1
    assert templates[0]["template_type"] == "NIFTY_ORB"
    assert "profit_protection_threshold" in templates[0]["parameters"]


def test_strategy_validate_valid(init_service):
    client = init_service
    payload = {
        "template_type": "NIFTY_ORB",
        "parameters": {
            "breakout_confirmation_mode": "CANDLE_CLOSE",
            "range_boundary_mode": "UP_TO_0944_CLOSE",
            "profit_trailing_mode": "LOCK_AND_TRAIL",
            "max_daily_loss": 3000.0,
        },
    }
    resp = client.post("/api/v1/strategies/validate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is True
    assert len(data["errors"]) == 0
    assert len(data["unresolved_decisions"]) >= 1


def test_strategy_validate_invalid(init_service):
    client = init_service
    payload = {
        "template_type": "NIFTY_ORB",
        "parameters": {
            "breakout_confirmation_mode": "INVALID_MODE",
        },
    }
    resp = client.post("/api/v1/strategies/validate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is False
    assert len(data["errors"]) > 0


def test_strategy_configs_crud(init_service):
    client = init_service
    # 1. List
    resp = client.get("/api/v1/strategies")
    assert resp.status_code == 200
    configs = resp.json()
    assert len(configs) >= 1

    # 2. Create new config
    create_payload = {
        "config_id": "TEST_NIFTY_V1",
        "template_type": "NIFTY_ORB",
        "underlying_symbol": "NSE:NIFTY50-INDEX",
        "parameters": {"max_daily_loss": 3500.0},
    }
    resp_create = client.post("/api/v1/strategies", json=create_payload)
    assert resp_create.status_code == 200
    created = resp_create.json()
    assert created["config_id"] == "TEST_NIFTY_V1"
    assert created["version"] >= 1

    # 3. Create new version
    version_payload = {
        "parameters": {"max_daily_loss": 4000.0},
        "set_active": True,
    }
    resp_ver = client.post("/api/v1/strategies/TEST_NIFTY_V1/versions", json=version_payload)
    assert resp_ver.status_code == 200
    updated = resp_ver.json()
    assert updated["config_id"] == "TEST_NIFTY_V1"
    assert updated["version"] == created["version"] + 1


def test_market_data_candles(init_service):
    client = init_service
    resp = client.get("/api/v1/market-data/candles?symbol=NSE:NIFTY50-INDEX&resolution=1m")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "NSE:NIFTY50-INDEX"
    assert data["candle_count"] > 0
    assert len(data["candles"]) > 0
    first_candle = data["candles"][0]
    assert "open" in first_candle
    assert "high" in first_candle
    assert "low" in first_candle
    assert "close" in first_candle


def test_market_data_instruments(init_service):
    client = init_service
    resp = client.get("/api/v1/market-data/instruments")
    assert resp.status_code == 200
    instruments = resp.json()
    assert len(instruments) >= 4
    symbols = [inst["symbol"] for inst in instruments]
    assert "NSE:NIFTY50-INDEX" in symbols
    assert "NSE:NIFTY26NOV25000CE" in symbols


def test_market_data_status(init_service):
    client = init_service
    resp = client.get("/api/v1/market-data/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in {"HEALTHY", "CLOSED", "UNAUTHENTICATED"}
    assert "feed_connected" in data
    assert "data_source" in data
    assert "fyers_authenticated" in data


def test_session_endpoints(init_service):
    client = init_service
    # Active
    resp = client.get("/api/v1/sessions/active")
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert data["execution_mode"] == "paper"
    assert "opening_range" in data
    assert "is_established" in data["opening_range"]

    # Start
    resp_start = client.post("/api/v1/sessions/start")
    assert resp_start.status_code == 200
    assert resp_start.json()["status"] == "STARTED"

    # Stop
    resp_stop = client.post("/api/v1/sessions/stop")
    assert resp_stop.status_code == 200
    assert resp_stop.json()["status"] == "STOPPED"


def test_risk_snapshot(init_service):
    client = init_service
    resp = client.get("/api/v1/risk/snapshot")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_mtm" in data
    assert "realized_pnl" in data
    assert "unrealized_pnl" in data
    assert "is_circuit_breaker_active" in data
    assert "profit_floor" in data


def test_orders_and_positions(init_service):
    client = init_service
    # Initial state on clean startup: 0 orders, 0 positions
    resp = client.get("/api/v1/orders")
    assert resp.status_code == 200
    assert len(resp.json()) == 0

    resp_pos = client.get("/api/v1/positions")
    assert resp_pos.status_code == 200
    assert len(resp_pos.json()) == 0

    # Seed demo fixtures explicitly
    resp_seed = client.post("/api/v1/sessions/seed-demo")
    assert resp_seed.status_code == 200
    assert resp_seed.json()["status"] == "SEEDED"

    # Verify seeded orders and positions
    resp_orders = client.get("/api/v1/orders")
    assert resp_orders.status_code == 200
    orders = resp_orders.json()
    assert len(orders) >= 1
    assert orders[0]["client_order_id"].startswith("ORD-")
    assert orders[0]["tag"] == "DEMO_FIXTURE"
    assert len(orders[0]["transitions"]) >= 1
    assert len(orders[0]["fills"]) >= 1

    resp_pos = client.get("/api/v1/positions")
    assert resp_pos.status_code == 200
    positions = resp_pos.json()
    assert len(positions) >= 1
    target_sym = positions[0]["symbol"]

    # Square-off single position
    resp_sq = client.post(f"/api/v1/positions/{target_sym}/square-off")
    assert resp_sq.status_code == 200
    sq_data = resp_sq.json()
    assert sq_data["is_open"] is False

    # Clear orders and positions
    resp_clear = client.post("/api/v1/sessions/clear-orders")
    assert resp_clear.status_code == 200
    assert resp_clear.json()["status"] == "CLEARED"

    # Confirm clean slate
    assert len(client.get("/api/v1/orders").json()) == 0
    assert len(client.get("/api/v1/positions").json()) == 0


def test_kill_switch(init_service):
    client = init_service
    req = {
        "reason": "MANUAL_OVERRIDE",
        "operator": "TestRunner",
        "notes": "Emergency liquidation test",
    }
    resp = client.post("/api/v1/risk/kill-switch", json=req)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["status"] == "TRIGGERED"

    # Check that risk engine reflects kill-switch state
    risk_resp = client.get("/api/v1/risk/snapshot")
    assert risk_resp.json()["is_kill_switch_active"] is True

    # Reset kill-switch so subsequent test suites remain isolated
    from src.api.service import workstation_service
    workstation_service.risk_engine.kill_switch.reset_for_new_session(workstation_service.active_session.session_id)


def test_backtest_endpoints(init_service):
    client = init_service
    # 1. Run backtest
    run_req = {
        "start_date": "2026-11-06",
        "end_date": "2026-11-06",
        "dataset_id": "TEST_DATASET_01",
        "slippage_points": 0.5,
        "brokerage_per_order": 20.0,
        "initial_capital": 100000.0,
    }
    resp = client.post("/api/v1/backtest/run", json=run_req)
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert "equity_curve" in data
    assert "trades" in data
    run_id = data["summary"]["run_id"]

    # 2. List runs
    resp_list = client.get("/api/v1/backtest/runs")
    assert resp_list.status_code == 200
    runs = resp_list.json()
    assert len(runs) >= 1

    # 3. Get single run
    resp_get = client.get(f"/api/v1/backtest/{run_id}")
    assert resp_get.status_code == 200
    assert resp_get.json()["summary"]["run_id"] == run_id

    # 4. StockMock Benchmark comparison
    csv_content = """Date,Time,Symbol,Type,Entry Price,Exit Price,PnL
2026-11-06,09:46:00,NSE:NIFTY26NOV25000CE,BUY,120.0,154.5,862.5
"""
    bench_req = {
        "run_id": run_id,
        "stockmock_csv_content": csv_content,
    }
    resp_bench = client.post("/api/v1/backtest/benchmark", json=bench_req)
    assert resp_bench.status_code == 200
    bench_data = resp_bench.json()
    assert "summary" in bench_data


def test_reconciliation_endpoints(init_service):
    client = init_service
    # Run reconciliation
    resp = client.post("/api/v1/reconciliation/run")
    assert resp.status_code == 200
    report = resp.json()
    assert "is_synchronized" in report

    # Latest reconciliation
    resp_latest = client.get("/api/v1/reconciliation/latest")
    assert resp_latest.status_code == 200
    assert "is_synchronized" in resp_latest.json()


def test_websocket_telemetry(init_service):
    client = init_service
    with client.websocket_connect("/ws/telemetry") as ws:
        # Initial snapshot
        data = ws.receive_json()
        assert data["event"] == "CONNECTED"
        assert "risk" in data
        assert "session" in data

        # Ping-pong
        ws.send_text("ping")
        resp = ws.receive_text()
        assert resp == "pong"
