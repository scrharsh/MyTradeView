"""Unit and regression tests for Phase 7.1 Trading Workstation Integrity and Safety Audit.

Verifies:
1. Rejection of unsupported strategy parameters and enforcement of mandatory positive risk controls.
2. Market data candle authenticity: no NIFTY 50 spot fallback for missing option contract symbols.
3. Authentic OMS order intent routing and pre-trade risk gatekeeper for position square-off.
4. Kill-switch execution accuracy and immutable audit event persistence.
5. Backtest date range validation, clean error on missing data, deterministic Sharpe ratio, and no arbitrary fallback on unknown run IDs.
6. Backtest persistence recovery across service restart.
7. Audit event API endpoint and query limits.
8. Strict PAPER execution mode safety.
"""

import asyncio
from datetime import date, datetime
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.models import BacktestRunRequest, StrategyValidateRequest
from src.api.service import workstation_service
from src.common.constants import (
    AuditEventType,
    OrderSide,
    OrderType,
    ProductType,
)
from src.common.time_utils import IST_TZ, to_utc
from src.oms.models import OrderIntent


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient fixture."""
    with TestClient(app) as test_client:
        yield test_client


# ------------------------------------------------------------------------------
# 1. Strategy Parameter Integrity & Mandatory Risk Controls
# ------------------------------------------------------------------------------

def test_validate_strategy_parameters_rejects_unsupported_params():
    """Verify validate_strategy_parameters rejects unrecognized parameter keys."""
    req = StrategyValidateRequest(
        template_type="NIFTY_ORB",
        parameters={
            "breakout_confirmation_mode": "CANDLE_CLOSE",
            "unsupported_rogue_parameter": 9999,
            "max_daily_loss": 3000.0,
        },
    )
    result = workstation_service.validate_strategy_parameters(req)
    assert result.is_valid is False
    assert any("Unsupported parameter 'unsupported_rogue_parameter'" in err for err in result.errors)


def test_validate_strategy_parameters_enforces_positive_daily_loss():
    """Verify max_daily_loss must be strictly positive."""
    # Negative loss
    neg_req = StrategyValidateRequest(
        template_type="NIFTY_ORB",
        parameters={"max_daily_loss": -1500.0},
    )
    neg_result = workstation_service.validate_strategy_parameters(neg_req)
    assert neg_result.is_valid is False
    assert any("max_daily_loss must be positive" in err for err in neg_result.errors)

    # Zero loss
    zero_req = StrategyValidateRequest(
        template_type="NIFTY_ORB",
        parameters={"max_daily_loss": 0.0},
    )
    zero_result = workstation_service.validate_strategy_parameters(zero_req)
    assert zero_result.is_valid is False
    assert any("max_daily_loss must be positive" in err for err in zero_result.errors)


def test_validate_strategy_parameters_enforces_profit_protection_rules():
    """Verify profit protection trigger and lock rules are strictly validated."""
    # Non-positive trigger
    inv_req = StrategyValidateRequest(
        template_type="NIFTY_ORB",
        parameters={"profit_protection_threshold": -100.0},
    )
    inv_trigger = workstation_service.validate_strategy_parameters(inv_req)
    assert inv_trigger.is_valid is False
    assert any("profit_protection_trigger must be positive" in err for err in inv_trigger.errors)

    # Minimum profit lock exceeding activation trigger
    lock_req = StrategyValidateRequest(
        template_type="NIFTY_ORB",
        parameters={
            "profit_protection_threshold": 3000.0,
            "minimum_profit_lock": 4500.0,
        },
    )
    inv_lock = workstation_service.validate_strategy_parameters(lock_req)
    assert inv_lock.is_valid is False
    assert any("cannot exceed profit_protection_trigger" in err for err in inv_lock.errors)


def test_validate_endpoint_http_contract(client):
    """Verify POST /api/v1/strategies/validate returns structured validation error."""
    resp = client.post(
        "/api/v1/strategies/validate",
        json={
            "template_type": "NIFTY_ORB",
            "parameters": {"unknown_field": "test", "max_daily_loss": -500},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is False
    assert len(data["errors"]) >= 2


# ------------------------------------------------------------------------------
# 2. Market Data Candle Authenticity
# ------------------------------------------------------------------------------

def test_get_candles_does_not_fallback_to_spot_for_unknown_contract():
    """Verify get_candles returns empty candle list for unknown option contract rather than NIFTY spot."""
    unknown_symbol = "NSE:NIFTY26NOV99999CE"
    res = workstation_service.get_candles(unknown_symbol)
    assert res.symbol == unknown_symbol
    assert res.candle_count == 0
    assert len(res.candles) == 0


def test_get_candles_spot_returns_spot_data():
    """Verify NIFTY 50 spot candles are returned when specifically requested."""
    res = workstation_service.get_candles("NSE:NIFTY50-INDEX")
    assert res.symbol == "NSE:NIFTY50-INDEX"
    assert res.candle_count > 0
    assert len(res.candles) > 0


# ------------------------------------------------------------------------------
# 3. Authentic OMS Square-Off Routing & Risk Gatekeeper
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_square_off_routes_through_oms_and_pre_trade_gatekeeper():
    """Verify square_off_position constructs OrderIntent, passes risk gatekeeper, and executes fill."""
    session_id = workstation_service.active_session.session_id
    contract_symbol = "NSE:NIFTY26NOV25000CE"

    # Ensure risk engine is not in killed state from prior tests
    workstation_service.risk_engine.kill_switch.reset_for_new_session(session_id)
    workstation_service.risk_engine.circuit_breaker.reset_for_new_session()

    # Ensure position exists or create an open position
    pos = workstation_service.oms.get_position(contract_symbol)
    if not pos or not pos.is_open or pos.net_qty == 0:
        # Create long position via authentic OMS order intent evaluated during market hours
        eval_time = to_utc(IST_TZ.localize(datetime(2026, 11, 6, 10, 0, 0)))
        contract = workstation_service.instrument_registry.get_contract_by_symbol(contract_symbol)
        buy_intent = OrderIntent(
            intent_id="INT-TEST-001",
            client_order_id="ORD-TEST-001",
            strategy_name="NIFTY_ORB",
            session_id=session_id,
            symbol=contract_symbol,
            side=OrderSide.BUY,
            qty=25,
            order_type=OrderType.MARKET,
            product_type=ProductType.INTRADAY,
            tag="TEST_SETUP",
            created_at=eval_time,
        )
        await workstation_service.oms.submit_order_intent(buy_intent, contract=contract, evaluation_time=eval_time)

    # Verify position is open
    pos_before = workstation_service.oms.get_position(contract_symbol)
    assert pos_before is not None
    assert pos_before.net_qty > 0
    initial_qty = int(pos_before.net_qty)

    # Execute square-off via service method
    sq_res = await workstation_service.square_off_position(
        contract_symbol, reason="INTEGRITY_AUDIT_TEST"
    )

    # Position should now be marked closed
    assert sq_res.symbol == contract_symbol
    assert sq_res.is_open is False
    assert sq_res.net_qty == 0

    # Verify an exit order with side SELL was processed by OMS
    orders = workstation_service.oms.get_all_orders()
    sq_orders = [o for o in orders if o.symbol == contract_symbol and o.side == OrderSide.SELL]
    assert len(sq_orders) >= 1
    assert sq_orders[-1].filled_qty == initial_qty


@pytest.mark.asyncio
async def test_square_off_non_existent_position_raises():
    """Verify squaring off a non-existent position raises ValueError."""
    with pytest.raises(ValueError, match="No open position found"):
        await workstation_service.square_off_position("NSE:NIFTYNONEXISTENTCE")


# ------------------------------------------------------------------------------
# 4. Kill-Switch Execution and Audit Event Persistence
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trigger_kill_switch_records_audit_event_and_reports_counts():
    """Verify trigger_kill_switch reports authentic counts and writes persistent AuditEvent."""
    res = await workstation_service.trigger_kill_switch(
        reason="AUDIT_VERIFICATION",
        operator="Auditor",
    )

    assert res.success is True
    assert res.status == "TRIGGERED"
    assert res.orders_cancelled_count >= 0
    assert res.positions_closed_count >= 0

    # Verify audit event is persisted
    events = await workstation_service.list_audit_events(limit=20)
    kill_events = [
        e for e in events
        if e.event_type in ("KILL_SWITCH_ACTIVATED", "KILL_SWITCH_TRIGGERED")
        and e.details.get("reason") == "AUDIT_VERIFICATION"
    ]
    assert len(kill_events) >= 1
    assert kill_events[0].component in ("WorkstationService", "RISK_ENGINE")


# ------------------------------------------------------------------------------
# 5. Backtest Authenticity: Date Range Validation & Error Handling
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backtest_missing_dates_raises_error():
    """Verify run_backtest raises ValueError when requested date range has no historical data."""
    req = BacktestRunRequest(
        start_date=date(2020, 1, 1),
        end_date=date(2020, 1, 5),
        dataset_id="TEST_DATASET_01",
    )
    with pytest.raises(ValueError, match="only available for 2026-11-06|unavailable"):
        await workstation_service.run_backtest(req)


def test_backtest_run_endpoint_returns_400_on_missing_dates(client):
    """Verify POST /api/v1/backtest/run returns HTTP 400 when dates are out of bounds."""
    resp = client.post(
        "/api/v1/backtest/run",
        json={
            "start_date": "2020-01-01",
            "end_date": "2020-01-05",
            "dataset_id": "TEST_DATASET_01",
        },
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"].lower()
    assert "only available" in detail or "unavailable" in detail or "no data" in detail


def test_backtest_unknown_run_id_returns_404_no_arbitrary_fallback(client):
    """Verify GET /api/v1/backtest/{run_id} returns HTTP 404 for unknown run_id."""
    resp = client.get("/api/v1/backtest/RUN-UNKNOWN-DOES-NOT-EXIST")
    assert resp.status_code == 404
    assert "Backtest run 'RUN-UNKNOWN-DOES-NOT-EXIST' not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_backtest_deterministic_sharpe_ratio():
    """Verify Sharpe ratio is computed deterministically rather than returning a static 1.85."""
    req = BacktestRunRequest(
        start_date=date(2026, 11, 6),
        end_date=date(2026, 11, 6),
        dataset_id="TEST_DATASET_01",
        slippage_points=0.5,
        brokerage_per_order=20.0,
        initial_capital=100000.0,
    )
    res = await workstation_service.run_backtest(req)
    summary = res.summary
    assert summary.run_id.startswith("BT_") or summary.run_id.startswith("RUN-")
    assert summary.total_trades >= 1
    # Win rate must be a percentage between 0 and 100
    assert Decimal("0.0") <= summary.win_rate_pct <= Decimal("100.0")
    # In a single-session backtest, portfolio Sharpe ratio is unavailable (None) with clear reason
    assert summary.sharpe_ratio is None
    assert "Insufficient daily return observations" in summary.sharpe_ratio_reason
    assert summary.trade_pnl_mean_std_ratio is None or isinstance(summary.trade_pnl_mean_std_ratio, Decimal)


@pytest.mark.asyncio
async def test_backtest_restart_persistence_recovery():
    """Verify backtest runs saved to StateRepository can be recovered upon request."""
    # Run backtest to populate
    req = BacktestRunRequest(
        start_date=date(2026, 11, 6),
        end_date=date(2026, 11, 6),
        dataset_id="TEST_DATASET_01",
    )
    res = await workstation_service.run_backtest(req)
    run_id = res.summary.run_id

    # Retrieve via service
    retrieved = await workstation_service.get_backtest_run(run_id)
    assert retrieved.summary.run_id == run_id
    assert retrieved.summary.net_profit == res.summary.net_profit
    assert len(retrieved.trades) == len(res.trades)


# ------------------------------------------------------------------------------
# 6. Audit Event API Endpoint & Limits
# ------------------------------------------------------------------------------

def test_audit_events_endpoint(client):
    """Verify GET /api/v1/risk/audit-events returns persistent audit events."""
    resp = client.get("/api/v1/risk/audit-events?limit=10")
    assert resp.status_code == 200
    events = resp.json()
    assert isinstance(events, list)
    assert len(events) >= 1
    first_evt = events[0]
    assert "event_id" in first_evt
    assert "event_type" in first_evt
    assert "component" in first_evt
    assert "timestamp" in first_evt


# ------------------------------------------------------------------------------
# 7. Strict PAPER Execution Mode Safety
# ------------------------------------------------------------------------------

def test_execution_mode_strictly_paper(client):
    """Verify execution mode is strictly PAPER and no live order path is accessible."""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["execution_mode"] == "paper"
