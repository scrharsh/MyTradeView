"""Unit tests for asynchronous database persistence and repository layer."""

from datetime import date, datetime, timezone
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.constants import (
    AuditEventType,
    ExecutionMode,
    OrderSide,
    OrderStatus,
    OrderType,
    ProductType,
    SessionPhase,
)
from src.core.models import (
    AuditEvent,
    BrokerOrder,
    DailyRiskState,
    Fill,
    Position,
    TradingSession,
)
from src.storage.repository import StateRepository


@pytest.mark.asyncio
async def test_session_lifecycle_persistence(state_repo: StateRepository):
    """Verify creating, querying, and updating trading session records."""
    sess = TradingSession(
        session_id="SESS_20261102",
        trading_date=date(2026, 11, 2),
        execution_mode=ExecutionMode.SIMULATION,
        phase=SessionPhase.PRE_MARKET,
    )
    await state_repo.save_session(sess)

    fetched = await state_repo.get_session("SESS_20261102")
    assert fetched is not None
    assert fetched.session_id == "SESS_20261102"
    assert fetched.phase == SessionPhase.PRE_MARKET

    # Update phase to ACTIVE_TRADING
    await state_repo.update_session_phase("SESS_20261102", SessionPhase.ACTIVE_TRADING)
    updated = await state_repo.get_session("SESS_20261102")
    assert updated.phase == SessionPhase.ACTIVE_TRADING


@pytest.mark.asyncio
async def test_order_and_fill_persistence(state_repo: StateRepository):
    """Verify persisting orders, updating status, recording fills, and tag queries."""
    sess = TradingSession(
        session_id="SESS_ORDER_TEST",
        trading_date=date(2026, 11, 2),
    )
    await state_repo.save_session(sess)

    order = BrokerOrder(
        client_order_id="CL_ORD_001",
        session_id="SESS_ORDER_TEST",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        product_type=ProductType.INTRADAY,
        qty=25,
        remaining_qty=25,
        tag="ORB_261102_01",
    )
    await state_repo.save_order(order)

    # Check query by tag (idempotency check)
    rec = await state_repo.get_order_by_tag("ORB_261102_01")
    assert rec is not None
    assert rec.client_order_id == "CL_ORD_001"

    # Record fill
    dt = datetime(2026, 11, 2, 9, 45, 1, tzinfo=timezone.utc)
    fill = Fill(
        fill_id="FILL_001",
        client_order_id="CL_ORD_001",
        broker_order_id="BRK_999",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.BUY,
        fill_price=102.50,
        fill_qty=25,
        timestamp=dt,
    )
    await state_repo.record_fill(fill)

    # Update order status to FILLED
    await state_repo.update_order_status(
        client_order_id="CL_ORD_001",
        status=OrderStatus.FILLED,
        filled_qty=25,
        remaining_qty=0,
        avg_fill_price=102.50,
        broker_order_id="BRK_999",
    )

    rec_updated = await state_repo.get_order_by_tag("ORB_261102_01")
    assert rec_updated.status == OrderStatus.FILLED.value
    assert rec_updated.filled_qty == 25
    assert rec_updated.avg_fill_price == 102.50


@pytest.mark.asyncio
async def test_position_persistence(state_repo: StateRepository):
    """Verify position saving and retrieval of open positions."""
    sess = TradingSession(
        session_id="SESS_POS_TEST",
        trading_date=date(2026, 11, 2),
    )
    await state_repo.save_session(sess)

    pos = Position(
        position_id="POS_001",
        session_id="SESS_POS_TEST",
        symbol="NSE:NIFTY26N0624500CE",
        net_qty=25,
        buy_qty=25,
        avg_buy_price=100.0,
        current_ltp=110.0,
        unrealized_pnl=250.0,
        is_open=True,
    )
    await state_repo.save_or_update_position(pos)

    open_positions = await state_repo.get_open_positions("SESS_POS_TEST")
    assert len(open_positions) == 1
    assert open_positions[0].symbol == "NSE:NIFTY26N0624500CE"
    assert open_positions[0].net_qty == 25

    # Close position and verify
    pos.net_qty = 0
    pos.sell_qty = 25
    pos.avg_sell_price = 112.0
    pos.realized_pnl = 300.0
    pos.unrealized_pnl = 0.0
    pos.is_open = False
    await state_repo.save_or_update_position(pos)

    open_after_close = await state_repo.get_open_positions("SESS_POS_TEST")
    assert len(open_after_close) == 0


@pytest.mark.asyncio
async def test_audit_event_and_daily_metrics(state_repo: StateRepository):
    """Verify recording audit events and risk snapshots."""
    sess = TradingSession(
        session_id="SESS_METRIC_TEST",
        trading_date=date(2026, 11, 2),
    )
    await state_repo.save_session(sess)

    event = AuditEvent(
        session_id="SESS_METRIC_TEST",
        event_type=AuditEventType.SIGNAL_EMITTED,
        component="StrategyEngine",
        details={"trigger": "ORB_HIGH_BREAKOUT", "nifty_spot": 24520.0},
    )
    await state_repo.record_audit_event(event)

    risk_state = DailyRiskState(
        session_id="SESS_METRIC_TEST",
        trading_date=date(2026, 11, 2),
        realized_pnl=500.0,
        unrealized_pnl=250.0,
        total_mtm=750.0,
        peak_mtm=800.0,
    )
    await state_repo.record_daily_metrics(risk_state, timestamp=datetime.now(timezone.utc))
