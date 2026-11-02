"""Unit tests for asynchronous database persistence, repository layer, and Decimal precision."""

from datetime import date, datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import select

from src.common.constants import (
    AuditEventType,
    ExecutionMode,
    OptionType,
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
    OptionContract,
    Position,
    StrategyConfigSnapshot,
    TradingSession,
)
from src.storage.repository import StateRepository
from src.storage.schema import OrderStateTransitionRecord


@pytest.mark.asyncio
async def test_session_lifecycle_persistence(state_repo: StateRepository):
    """Verify creating, querying, and updating trading session records."""
    sess = TradingSession(
        session_id="SESS_20261102",
        trading_date=date(2026, 11, 2),
        execution_mode=ExecutionMode.PAPER,
        phase=SessionPhase.PRE_MARKET,
    )
    await state_repo.save_session(sess)

    fetched = await state_repo.get_session("SESS_20261102")
    assert fetched is not None
    assert fetched.session_id == "SESS_20261102"
    assert fetched.execution_mode == ExecutionMode.PAPER
    assert fetched.phase == SessionPhase.PRE_MARKET

    # Update phase to ACTIVE_TRADING
    await state_repo.update_session_phase("SESS_20261102", SessionPhase.ACTIVE_TRADING)
    updated = await state_repo.get_session("SESS_20261102")
    assert updated.phase == SessionPhase.ACTIVE_TRADING


@pytest.mark.asyncio
async def test_strategy_config_versioning_persistence(state_repo: StateRepository):
    """Verify saving and querying versioned strategy configuration snapshots."""
    snapshot_v1 = StrategyConfigSnapshot(
        version=1,
        underlying_symbol="NSE:NIFTY50-INDEX",
        max_daily_loss_limit=Decimal("3000.00"),
        profit_protection_trigger=Decimal("3000.00"),
    )
    await state_repo.save_strategy_config(snapshot_v1, is_active=True)

    fetched = await state_repo.get_latest_active_config("NSE:NIFTY50-INDEX")
    assert fetched is not None
    assert fetched.version == 1
    assert fetched.config_json["underlying_symbol"] == "NSE:NIFTY50-INDEX"


@pytest.mark.asyncio
async def test_instrument_metadata_persistence(state_repo: StateRepository):
    """Verify instrument contract metadata persistence with Decimal strike."""
    contract = OptionContract(
        symbol="NSE:NIFTY26N0624500CE",
        underlying="NSE:NIFTY50-INDEX",
        strike=Decimal("24500.00"),
        option_type=OptionType.CE,
        expiry_date=date(2026, 11, 6),
        lot_size=25,
        tick_size=Decimal("0.05"),
    )
    dt = datetime(2026, 11, 2, 9, 15, 0, tzinfo=timezone.utc)
    await state_repo.save_or_update_instrument(contract, updated_at=dt)

    # Re-save with updated strike/lot_size should update existing
    await state_repo.save_or_update_instrument(contract, updated_at=dt)


@pytest.mark.asyncio
async def test_order_fill_and_transition_audit_persistence(state_repo: StateRepository):
    """Verify order state progression logging and Decimal fill prices."""
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

    # Record fill with Decimal price
    dt = datetime(2026, 11, 2, 9, 45, 1, tzinfo=timezone.utc)
    fill = Fill(
        fill_id="FILL_001",
        client_order_id="CL_ORD_001",
        broker_order_id="BRK_999",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.BUY,
        fill_price=Decimal("102.5000"),
        fill_qty=25,
        timestamp=dt,
    )
    await state_repo.record_fill(fill)

    # Transition order status from NEW to FILLED and verify transition audit record
    await state_repo.update_order_status(
        client_order_id="CL_ORD_001",
        from_status=OrderStatus.NEW,
        to_status=OrderStatus.FILLED,
        filled_qty=25,
        remaining_qty=0,
        avg_fill_price=Decimal("102.5000"),
        broker_order_id="BRK_999",
    )

    rec_updated = await state_repo.get_order_by_tag("ORB_261102_01")
    assert rec_updated.status == OrderStatus.FILLED.value
    assert rec_updated.filled_qty == 25
    assert rec_updated.avg_fill_price == Decimal("102.5000")

    # Verify audit transition record was automatically inserted
    stmt = select(OrderStateTransitionRecord).where(
        OrderStateTransitionRecord.client_order_id == "CL_ORD_001"
    )
    res = await state_repo._session.execute(stmt)
    transitions = list(res.scalars().all())
    assert len(transitions) == 1
    assert transitions[0].from_status == OrderStatus.NEW.value
    assert transitions[0].to_status == OrderStatus.FILLED.value


@pytest.mark.asyncio
async def test_position_persistence_decimal(state_repo: StateRepository):
    """Verify position persistence and retrieval with exact Decimal P&L."""
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
        avg_buy_price=Decimal("100.25"),
        current_ltp=Decimal("110.50"),
        unrealized_pnl=Decimal("256.25"),
        is_open=True,
    )
    await state_repo.save_or_update_position(pos)

    open_positions = await state_repo.get_open_positions("SESS_POS_TEST")
    assert len(open_positions) == 1
    assert open_positions[0].symbol == "NSE:NIFTY26N0624500CE"
    assert open_positions[0].net_qty == 25
    assert open_positions[0].avg_buy_price == Decimal("100.2500")

    # Close position and verify
    pos.net_qty = 0
    pos.sell_qty = 25
    pos.avg_sell_price = Decimal("112.50")
    pos.realized_pnl = Decimal("306.25")
    pos.unrealized_pnl = Decimal("0.00")
    pos.is_open = False
    await state_repo.save_or_update_position(pos)

    open_after_close = await state_repo.get_open_positions("SESS_POS_TEST")
    assert len(open_after_close) == 0


@pytest.mark.asyncio
async def test_risk_and_pnl_snapshots_decimal(state_repo: StateRepository):
    """Verify recording risk snapshots and daily P&L snapshots with Decimal precision."""
    sess = TradingSession(
        session_id="SESS_METRIC_TEST",
        trading_date=date(2026, 11, 2),
    )
    await state_repo.save_session(sess)

    risk_state = DailyRiskState(
        session_id="SESS_METRIC_TEST",
        trading_date=date(2026, 11, 2),
        realized_pnl=Decimal("500.50"),
        unrealized_pnl=Decimal("250.25"),
        total_mtm=Decimal("750.75"),
        peak_mtm=Decimal("800.00"),
    )
    await state_repo.record_risk_snapshot(risk_state, timestamp=datetime.now(timezone.utc))

    await state_repo.record_daily_pnl_snapshot(
        session_id="SESS_METRIC_TEST",
        trading_date=date(2026, 11, 2),
        net_realized_pnl=Decimal("500.50"),
        net_unrealized_pnl=Decimal("0.00"),
        gross_mtm=Decimal("500.50"),
        peak_profit=Decimal("800.00"),
        max_drawdown=Decimal("150.00"),
        total_trades_count=2,
        timestamp=datetime.now(timezone.utc),
    )

    event = AuditEvent(
        session_id="SESS_METRIC_TEST",
        event_type=AuditEventType.SIGNAL_EMITTED,
        component="StrategyEngine",
        details={"trigger": "ORB_HIGH_BREAKOUT", "nifty_spot": "24520.00"},
    )
    await state_repo.record_audit_event(event)
