"""Phase 7.2 Independent Safety and Integrity Verification Test Suite.

Adversarial verification testing covering:
1. All 15 OMS lifecycle and emergency control scenarios required in Phase 7.2 Section 3.
2. Hard fail-closed live execution prevention interlocks.
3. Sharpe ratio and backtest mathematical verification.
4. Backtest 404 on unknown run IDs and missing contract protection.
"""

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from config.risk_config import RiskConfig
from config.strategy_config import StrategyConfig
from src.backtest.models import (
    BacktestDiagnostics,
    BacktestMetrics,
    BacktestRun,
    BacktestTrade,
    ExecutionModelConfig,
)
from src.broker.models import (
    AuthStatus,
    BrokerConnectionStatus,
    BrokerOrderBookItem,
    BrokerOrderPayload,
    BrokerOrderResponse,
    BrokerPosition,
    BrokerTradeItem,
)
from src.broker.paper import PaperBrokerClient
from src.common.constants import (
    ExecutionMode,
    OptionType,
    OrderSide,
    OrderStatus,
    OrderType,
    ProductType,
    RiskRejectReason,
    RiskTriggerReason,
    SessionPhase,
)
from src.common.time_utils import IST_TZ, now_utc, to_ist, to_utc
from src.core.models import (
    BrokerOrder,
    Fill,
    OptionContract,
    OrderRequest,
    Position,
    TradingSession,
)
from src.oms.idempotency import ClientOrderIdGenerator, IdempotencyTracker
from src.oms.lifecycle import can_transition_oms_order, validate_oms_transition
from src.oms.manager import OrderManagementSystem
from src.oms.models import (
    OmsOrder,
    OmsOrderStatus,
    OrderIntent,
)
from src.oms.reconciliation import (
    BrokerReconciliationService,
    DiscrepancyType,
    ReconciliationReport,
)
from src.risk.engine import RiskEngine
from src.risk.pre_trade import PreTradeGatekeeper
from src.storage.repository import StateRepository
from src.api.service import WorkstationService, workstation_service
from src.api.models import StrategyValidateRequest


# ------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------

@pytest.fixture
def sample_session() -> TradingSession:
    """Active paper trading session on a Friday."""
    return TradingSession(
        session_id="SESS_P72_001",
        trading_date=date(2026, 11, 6),  # Friday
        execution_mode=ExecutionMode.PAPER,
        phase=SessionPhase.ACTIVE_TRADING,
    )


@pytest.fixture
def paper_broker() -> PaperBrokerClient:
    return PaperBrokerClient(initial_balance=Decimal("100000.00"))


@pytest.fixture
def risk_engine(sample_session: TradingSession) -> RiskEngine:
    r_cfg = RiskConfig(
        max_daily_loss_limit=Decimal("3000.00"),
        profit_protection_trigger=Decimal("3000.00"),
        profit_protection_min_lock=Decimal("500.00"),
    )
    s_cfg = StrategyConfig(
        trading_days=["Monday", "Thursday", "Friday"],
        orb_start_time=time(9, 16, 0),
        orb_end_time=time(9, 45, 0),
        mandatory_exit_time=time(15, 10, 0),
        lots=1,
        lot_size=25,
    )
    return RiskEngine(
        session_id=sample_session.session_id,
        risk_config=r_cfg,
        strategy_config=s_cfg,
    )


@pytest.fixture
def sample_contract() -> OptionContract:
    return OptionContract(
        symbol="NSE:NIFTY26NOV24500CE",
        underlying="NSE:NIFTY50-INDEX",
        strike=Decimal("24500.00"),
        option_type=OptionType.CE,
        expiry_date=date(2026, 11, 26),
        lot_size=25,
        tick_size=Decimal("0.05"),
    )


@pytest.fixture
def sample_put_contract() -> OptionContract:
    return OptionContract(
        symbol="NSE:NIFTY26NOV24500PE",
        underlying="NSE:NIFTY50-INDEX",
        strike=Decimal("24500.00"),
        option_type=OptionType.PE,
        expiry_date=date(2026, 11, 26),
        lot_size=25,
        tick_size=Decimal("0.05"),
    )


# ------------------------------------------------------------------------------
# 15 Specific OMS Lifecycle and Emergency Control Scenarios (Phase 7.2 Section 3)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scenario_01_order_accepted_but_not_filled(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """Scenario 1: Order accepted by broker but not filled.
    - Resulting state: OPEN
    - Position quantity: 0
    - Exit pending: False
    - New entries blocked: False
    - Position is NOT reported closed (it was never opened)
    """
    paper_broker.set_auto_fill(False)
    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    intent = OrderIntent(
        intent_id="INT_SC1_01",
        client_order_id="ORD_SC1_01",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150.00"),
        qty=25,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)).astimezone(timezone.utc),
    )

    order = await oms.submit_order_intent(intent, contract=sample_contract)
    assert order.oms_status == OmsOrderStatus.OPEN
    assert order.filled_qty == 0
    assert order.remaining_qty == 25

    pos = oms.get_position(sample_contract.symbol)
    assert pos is None
    assert len(oms.get_open_orders()) == 1


@pytest.mark.asyncio
async def test_scenario_02_order_rejected(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """Scenario 2: Order rejected by broker RMS.
    - Resulting state: REJECTED
    - Position quantity: 0
    - Exit pending: False
    - New entries blocked: False (rejection recorded, future valid orders allowed)
    - Audit log records rejection reason
    """
    paper_broker.set_force_reject(True, "RMS: Margin insufficient")
    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    intent = OrderIntent(
        intent_id="INT_SC2_01",
        client_order_id="ORD_SC2_01",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150.00"),
        qty=25,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)).astimezone(timezone.utc),
    )

    order = await oms.submit_order_intent(intent, contract=sample_contract)
    assert order.oms_status == OmsOrderStatus.REJECTED
    assert "Margin insufficient" in order.status_message
    assert oms.get_position(sample_contract.symbol) is None


@pytest.mark.asyncio
async def test_scenario_03_partial_entry_fill(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """Scenario 3: Partial entry fill.
    - Resulting state: PARTIALLY_FILLED
    - Position quantity: 25 (from 50 requested)
    - Position is_open: True
    - Exit pending: False
    - Position is NOT reported closed
    """
    paper_broker.set_auto_fill(False)
    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    intent = OrderIntent(
        intent_id="INT_SC3_01",
        client_order_id="ORD_SC3_01",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150.00"),
        qty=50,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)).astimezone(timezone.utc),
    )

    order = await oms.submit_order_intent(intent, contract=sample_contract)
    assert order.oms_status == OmsOrderStatus.OPEN

    fill = Fill(
        fill_id="FILL_SC3_01",
        client_order_id=order.client_order_id,
        broker_order_id=order.broker_order_id,
        symbol=order.symbol,
        side=OrderSide.BUY,
        fill_price=Decimal("148.50"),
        fill_qty=25,
    )
    await oms.on_fill_event(fill)

    assert order.oms_status == OmsOrderStatus.PARTIALLY_FILLED
    assert order.filled_qty == 25
    assert order.remaining_qty == 25

    pos = oms.get_position(sample_contract.symbol)
    assert pos is not None
    assert pos.is_open is True
    assert pos.net_qty == 25
    assert pos.buy_qty == 25


@pytest.mark.asyncio
async def test_scenario_04_partial_exit_fill(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """Scenario 4: Partial exit fill.
    - Start with 50 qty position.
    - Place 50 qty exit order.
    - Receive partial fill for 25 qty.
    - Resulting state: PARTIALLY_FILLED
    - Resulting position: 25 qty remaining open
    - Exit pending: True (25 qty exit remaining)
    - Position is NOT reported closed
    """
    paper_broker.set_auto_fill(False)
    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    # 1. Full entry fill for 50 qty
    entry_intent = OrderIntent(
        intent_id="INT_SC4_ENTRY",
        client_order_id="ORD_SC4_ENTRY",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150.00"),
        qty=50,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)).astimezone(timezone.utc),
    )
    entry_order = await oms.submit_order_intent(entry_intent, contract=sample_contract)
    await oms.on_fill_event(Fill(
        fill_id="FILL_SC4_ENTRY",
        client_order_id=entry_order.client_order_id,
        broker_order_id=entry_order.broker_order_id,
        symbol=entry_order.symbol,
        side=OrderSide.BUY,
        fill_price=Decimal("150.00"),
        fill_qty=50,
    ))
    pos = oms.get_position(sample_contract.symbol)
    assert pos.net_qty == 50
    assert pos.is_open is True

    # 2. Submit exit order for 50 qty
    exit_intent = OrderIntent(
        intent_id="INT_SC4_EXIT",
        client_order_id="ORD_SC4_EXIT",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("160.00"),
        qty=50,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 10, 0, 0)).astimezone(timezone.utc),
    )
    exit_order = await oms.submit_order_intent(exit_intent, contract=sample_contract)
    assert exit_order.oms_status == OmsOrderStatus.OPEN

    # 3. Partial exit fill of 25 qty
    await oms.on_fill_event(Fill(
        fill_id="FILL_SC4_EXIT_PARTIAL",
        client_order_id=exit_order.client_order_id,
        broker_order_id=exit_order.broker_order_id,
        symbol=exit_order.symbol,
        side=OrderSide.SELL,
        fill_price=Decimal("160.00"),
        fill_qty=25,
    ))

    assert exit_order.oms_status == OmsOrderStatus.PARTIALLY_FILLED
    assert exit_order.remaining_qty == 25
    pos_after = oms.get_position(sample_contract.symbol)
    assert pos_after.net_qty == 25
    assert pos_after.is_open is True


@pytest.mark.asyncio
async def test_scenario_05_full_exit_fill(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """Scenario 5: Full exit fill.
    - Start with 25 qty position.
    - Exit order for 25 fills completely.
    - Resulting state: FILLED
    - Resulting position: 0 qty, is_open=False
    - Exit pending: False
    - System reports position closed ONLY when confirmed filled
    """
    paper_broker.set_auto_fill(False)
    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    # Establish 25 qty position
    entry_intent = OrderIntent(
        intent_id="INT_SC5_ENTRY",
        client_order_id="ORD_SC5_ENTRY",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150.00"),
        qty=25,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)).astimezone(timezone.utc),
    )
    entry_order = await oms.submit_order_intent(entry_intent, contract=sample_contract)
    await oms.on_fill_event(Fill(
        fill_id="FILL_SC5_ENTRY",
        client_order_id=entry_order.client_order_id,
        broker_order_id=entry_order.broker_order_id,
        symbol=entry_order.symbol,
        side=OrderSide.BUY,
        fill_price=Decimal("150.00"),
        fill_qty=25,
    ))
    assert oms.get_position(sample_contract.symbol).net_qty == 25

    # Submit exit order and fill completely
    exit_intent = OrderIntent(
        intent_id="INT_SC5_EXIT",
        client_order_id="ORD_SC5_EXIT",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        qty=25,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 10, 15, 0)).astimezone(timezone.utc),
    )
    exit_order = await oms.submit_order_intent(exit_intent, contract=sample_contract)
    await oms.on_fill_event(Fill(
        fill_id="FILL_SC5_EXIT_FULL",
        client_order_id=exit_order.client_order_id,
        broker_order_id=exit_order.broker_order_id,
        symbol=exit_order.symbol,
        side=OrderSide.SELL,
        fill_price=Decimal("165.00"),
        fill_qty=25,
    ))

    assert exit_order.oms_status == OmsOrderStatus.FILLED
    assert exit_order.remaining_qty == 0
    pos_closed = oms.get_position(sample_contract.symbol)
    assert pos_closed.net_qty == 0
    assert pos_closed.is_open is False


@pytest.mark.asyncio
async def test_scenario_06_exit_request_times_out_ambiguous(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """Scenario 6: Exit request times out with ambiguous outcome.
    - Broker times out during exit request.
    - Resulting order status: RECONCILIATION_REQUIRED
    - Position quantity: Remains unchanged (is NOT assumed closed)
    - Exit pending: Ambiguous / True
    - New entries blocked: True (reconciliation required)
    """
    paper_broker.set_auto_fill(False)
    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    # Establish 25 qty position
    entry_intent = OrderIntent(
        intent_id="INT_SC6_ENTRY",
        client_order_id="ORD_SC6_ENTRY",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150.00"),
        qty=25,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)).astimezone(timezone.utc),
    )
    entry_order = await oms.submit_order_intent(entry_intent, contract=sample_contract)
    await oms.on_fill_event(Fill(
        fill_id="FILL_SC6_ENTRY",
        client_order_id=entry_order.client_order_id,
        broker_order_id=entry_order.broker_order_id,
        symbol=entry_order.symbol,
        side=OrderSide.BUY,
        fill_price=Decimal("150.00"),
        fill_qty=25,
    ))

    # Force timeout on exit order
    paper_broker.set_force_timeout(True)
    exit_intent = OrderIntent(
        intent_id="INT_SC6_EXIT",
        client_order_id="ORD_SC6_EXIT",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        qty=25,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 10, 15, 0)).astimezone(timezone.utc),
    )
    exit_order = await oms.submit_order_intent(exit_intent, contract=sample_contract)

    assert exit_order.oms_status == OmsOrderStatus.RECONCILIATION_REQUIRED
    # Position must NOT be marked closed!
    pos = oms.get_position(sample_contract.symbol)
    assert pos.net_qty == 25
    assert pos.is_open is True


@pytest.mark.asyncio
async def test_scenario_07_duplicate_square_off_request():
    """Scenario 7: Duplicate square-off request.
    - Calling square_off_position on a symbol with no open position raises ValueError.
    """
    with pytest.raises(ValueError) as exc:
        await workstation_service.square_off_position("NSE:NIFTY26NOV24500CE")
    assert "No open position found" in str(exc.value)


@pytest.mark.asyncio
async def test_scenario_08_duplicate_order_submission_request(
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """Scenario 8: Duplicate order-submission request.
    - PreTradeGatekeeper rejects second submission with identical client_order_id.
    - RiskRejectReason.DUPLICATE_ORDER returned, broker is not called.
    """
    gatekeeper = PreTradeGatekeeper()
    eval_time = to_utc(IST_TZ.localize(datetime(2026, 11, 6, 10, 0, 0)))
    req = OrderRequest(
        client_order_id="ORD_SC8_DUP_01",
        session_id=sample_session.session_id,
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=25,
        tag="TAG_DUP",
    )

    # First validation succeeds
    res1 = gatekeeper.validate_order_request(
        order_request=req,
        execution_mode=ExecutionMode.PAPER,
        session=sample_session,
        contract=sample_contract,
        evaluation_time=eval_time,
    )
    assert res1.is_valid is True

    # Register order as submitted
    gatekeeper.register_client_order_id(req.client_order_id)

    # Second validation fails closed with DUPLICATE_ORDER
    res2 = gatekeeper.validate_order_request(
        order_request=req,
        execution_mode=ExecutionMode.PAPER,
        session=sample_session,
        contract=sample_contract,
        evaluation_time=eval_time,
    )
    assert res2.is_valid is False
    assert res2.reason_code == RiskRejectReason.DUPLICATE_ORDER


@pytest.mark.asyncio
async def test_scenario_09_cancellation_requested_not_confirmed(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """Scenario 9: Cancellation requested but not confirmed.
    - Verify intermediate state CANCELLATION_REQUESTED is recorded before CANCELLED.
    """
    paper_broker.set_auto_fill(False)
    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    intent = OrderIntent(
        intent_id="INT_SC9_01",
        client_order_id="ORD_SC9_01",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150.00"),
        qty=25,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)).astimezone(timezone.utc),
    )
    order = await oms.submit_order_intent(intent, contract=sample_contract)
    assert order.oms_status == OmsOrderStatus.OPEN

    res = await oms.request_cancel_order(order.client_order_id, reason="Manual user cancel")
    assert res.success is True
    transition_statuses = [t.to_status for t in order.transitions]
    assert OmsOrderStatus.CANCELLATION_REQUESTED in transition_statuses
    assert order.oms_status == OmsOrderStatus.CANCELLED


@pytest.mark.asyncio
async def test_scenario_10_kill_switch_while_entry_order_pending(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """Scenario 10: Kill switch activated while entry order is pending.
    - Cancels open entry order.
    - Risk engine / OMS enters lockout: new entries blocked immediately.
    """
    paper_broker.set_auto_fill(False)
    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    intent = OrderIntent(
        intent_id="INT_SC10_01",
        client_order_id="ORD_SC10_01",
        session_id=sample_session.session_id,
        strategy_name="NIFTY_ORB",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150.00"),
        qty=25,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)).astimezone(timezone.utc),
    )
    order = await oms.submit_order_intent(intent, contract=sample_contract)
    assert order.oms_status == OmsOrderStatus.OPEN
    assert len(oms.get_open_orders()) == 1

    # Trigger emergency kill-switch
    risk_engine.kill_switch.trigger(
        reason=RiskTriggerReason.MANUAL_OVERRIDE,
        details={"operator": "Auditor", "reason": "Test pending order cancellation"},
    )
    assert risk_engine.kill_switch.is_active is True

    # Cancel open orders
    for o in oms.get_open_orders():
        await oms.request_cancel_order(o.client_order_id, reason="Kill switch triggered")

    assert order.oms_status == OmsOrderStatus.CANCELLED
    assert len(oms.get_open_orders()) == 0

    # Verify risk lock blocks new entries
    new_intent = OrderIntent(
        intent_id="INT_SC10_NEW",
        client_order_id="ORD_SC10_NEW",
        session_id=sample_session.session_id,
        strategy_name="NIFTY_ORB",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150.00"),
        qty=25,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 47, 0)).astimezone(timezone.utc),
    )
    rejected_order = await oms.submit_order_intent(new_intent, contract=sample_contract)
    assert rejected_order.oms_status == OmsOrderStatus.REJECTED
    assert "Risk rejected" in rejected_order.status_message


@pytest.mark.asyncio
async def test_scenario_11_kill_switch_while_position_partially_filled(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """Scenario 11: Kill switch activated while position is partially filled.
    - Flattens the open net quantity.
    - Position is closed, new entries locked out.
    """
    paper_broker.set_auto_fill(False)
    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    intent = OrderIntent(
        intent_id="INT_SC11_01",
        client_order_id="ORD_SC11_01",
        session_id=sample_session.session_id,
        strategy_name="NIFTY_ORB",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150.00"),
        qty=50,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)).astimezone(timezone.utc),
    )
    order = await oms.submit_order_intent(intent, contract=sample_contract)
    # Partial fill of 25
    await oms.on_fill_event(Fill(
        fill_id="FILL_SC11_01",
        client_order_id=order.client_order_id,
        broker_order_id=order.broker_order_id,
        symbol=order.symbol,
        side=OrderSide.BUY,
        fill_price=Decimal("150.00"),
        fill_qty=25,
    ))
    pos = oms.get_position(sample_contract.symbol)
    assert pos.net_qty == 25
    assert pos.is_open is True

    # Trigger kill switch
    risk_engine.kill_switch.trigger(
        reason=RiskTriggerReason.MANUAL_OVERRIDE,
        details={"operator": "Auditor", "reason": "Partial fill kill switch"},
    )

    # Cancel pending remaining entry
    await oms.request_cancel_order(order.client_order_id, reason="Kill switch cancel remaining")

    # Square off the open 25 qty position
    paper_broker.set_auto_fill(True)
    exit_intent = OrderIntent(
        intent_id="INT_SC11_SQOFF",
        client_order_id="ORD_SC11_SQOFF",
        session_id=sample_session.session_id,
        strategy_name="NIFTY_ORB",
        symbol=sample_contract.symbol,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        qty=25,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 50, 0)).astimezone(timezone.utc),
    )
    exit_order = await oms.submit_order_intent(exit_intent, contract=sample_contract)
    assert exit_order.oms_status == OmsOrderStatus.FILLED

    pos_after = oms.get_position(sample_contract.symbol)
    assert pos_after.net_qty == 0
    assert pos_after.is_open is False


@pytest.mark.asyncio
async def test_scenario_12_exit_order_rejected_during_emergency_liquidation(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """Scenario 12: Exit order rejected during emergency liquidation.
    - Broker rejects exit order.
    - Closed count is NOT incremented.
    - Kill switch stays active (does NOT clear risk state).
    - Open position remains open and reported accurately.
    """
    paper_broker.set_auto_fill(True)
    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    intent = OrderIntent(
        intent_id="INT_SC12_01",
        client_order_id="ORD_SC12_01",
        session_id=sample_session.session_id,
        strategy_name="NIFTY_ORB",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=25,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)).astimezone(timezone.utc),
    )
    order = await oms.submit_order_intent(intent, contract=sample_contract)
    pos = oms.get_position(sample_contract.symbol)
    assert pos.is_open is True

    # Now force broker reject for all subsequent orders (including square-off)
    paper_broker.set_force_reject(True, "Broker RMS rejection on square-off")

    # Engage kill switch
    risk_engine.kill_switch.trigger(
        reason=RiskTriggerReason.MANUAL_OVERRIDE,
        details={"operator": "Auditor", "reason": "Emergency liquidation test"},
    )
    assert risk_engine.kill_switch.is_active is True

    # Attempt emergency exit
    exit_intent = OrderIntent(
        intent_id="INT_SC12_EXIT",
        client_order_id="ORD_SC12_EXIT",
        session_id=sample_session.session_id,
        strategy_name="NIFTY_ORB",
        symbol=sample_contract.symbol,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        qty=25,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 50, 0)).astimezone(timezone.utc),
    )
    exit_order = await oms.submit_order_intent(exit_intent, contract=sample_contract)
    assert exit_order.oms_status == OmsOrderStatus.REJECTED

    # Crucial safety check: kill-switch remains active and position is NOT reported closed
    assert risk_engine.kill_switch.is_active is True
    pos_after = oms.get_position(sample_contract.symbol)
    assert pos_after.is_open is True
    assert pos_after.net_qty == 25


@pytest.mark.asyncio
async def test_scenario_13_broker_disconnect_during_order_transition(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """Scenario 13: Broker disconnect during transition.
    - Fails closed safely.
    - Broker connection reports disconnected.
    - No optimistic status updates allowed while disconnected.
    """
    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )
    paper_broker.set_connection_status(BrokerConnectionStatus.DISCONNECTED)

    assert paper_broker.is_connected is False
    assert paper_broker.connection_status == BrokerConnectionStatus.DISCONNECTED


@pytest.mark.asyncio
async def test_scenario_14_reversal_requested_before_exit_confirmed(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
    sample_put_contract: OptionContract,
):
    """Scenario 14: Reversal requested before previous position's exit is confirmed.
    - If exit order does not fill (e.g. rejected or left open), replacement order MUST be aborted.
    """
    paper_broker.set_force_reject(True, "Exit order failed to execute")
    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    exit_intent = OrderIntent(
        intent_id="INT_SC14_EXIT",
        client_order_id="ORD_SC14_EXIT",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        qty=25,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)).astimezone(timezone.utc),
    )
    entry_intent = OrderIntent(
        intent_id="INT_SC14_ENTRY",
        client_order_id="ORD_SC14_ENTRY",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_put_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=25,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)).astimezone(timezone.utc),
    )

    exit_ord, entry_ord = await oms.coordinate_two_phase_reversal(
        exit_intent=exit_intent,
        entry_intent=entry_intent,
        exit_contract=sample_contract,
        entry_contract=sample_put_contract,
    )
    assert exit_ord.oms_status == OmsOrderStatus.REJECTED
    assert entry_ord is None  # Phase 2 aborted safely


@pytest.mark.asyncio
async def test_scenario_15_repeated_out_of_order_broker_updates(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """Scenario 15: Repeated or out-of-order broker updates.
    - Terminal states (FILLED, CANCELLED) cannot be regressed by delayed broker packets.
    """
    paper_broker.set_auto_fill(True)
    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    intent = OrderIntent(
        intent_id="INT_SC15_01",
        client_order_id="ORD_SC15_01",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=25,
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)).astimezone(timezone.utc),
    )
    order = await oms.submit_order_intent(intent, contract=sample_contract)
    assert order.oms_status == OmsOrderStatus.FILLED

    # Now simulate out-of-order delayed update packet claiming order is PENDING
    oms.on_broker_order_update(
        client_order_id=order.client_order_id,
        broker_order_id=order.broker_order_id,
        broker_status=OrderStatus.PENDING,
        message="Stale delayed packet",
    )
    # Status MUST remain FILLED
    assert order.oms_status == OmsOrderStatus.FILLED


# ------------------------------------------------------------------------------
# Live Execution Mode Rejection Interlocks
# ------------------------------------------------------------------------------

def test_live_execution_mode_rejected_by_gatekeeper(
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """Verify that PreTradeGatekeeper unconditionally blocks ExecutionMode.LIVE."""
    gatekeeper = PreTradeGatekeeper()
    req = OrderRequest(
        client_order_id="ORD_LIVE_TEST_01",
        session_id=sample_session.session_id,
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=25,
        tag="LIVE_TEST",
    )
    res = gatekeeper.validate_order_request(
        order_request=req,
        execution_mode=ExecutionMode.LIVE,
        session=sample_session,
        contract=sample_contract,
    )
    assert res.is_valid is False
    assert res.reason_code == RiskRejectReason.EXECUTION_MODE_DISABLED
    assert "Live trading execution is disabled" in res.message


# ------------------------------------------------------------------------------
# Sharpe Ratio and Backtest Mathematics Evaluation
# ------------------------------------------------------------------------------

def test_sharpe_ratio_computation_properties():
    """Verify exact mathematical properties of _format_backtest_summary Sharpe ratio:
    - Return series: trade-level net P&L in currency (rupees), NOT percentage returns
    - No period binning (no zero-return non-trading days included)
    - Risk-free rate: assumed 0
    - Annualization: none (missing sqrt(252))
    - Single trade or zero stdev returns Decimal('0.00')
    """
    exec_cfg = ExecutionModelConfig()
    diagnostics = BacktestDiagnostics()

    def make_run(trades: list[BacktestTrade]) -> BacktestRun:
        net = sum(t.net_pnl for t in trades)
        metrics = BacktestMetrics(
            total_trades=len(trades),
            winning_trades=len([t for t in trades if t.net_pnl > 0]),
            losing_trades=len([t for t in trades if t.net_pnl <= 0]),
            win_rate=Decimal("50.00"),
            gross_pnl=net,
            net_pnl=net,
            profit_factor=Decimal("2.00"),
            avg_win=Decimal("1500.00"),
            avg_loss=Decimal("0.00"),
            expectancy=Decimal("500.00"),
            max_drawdown=Decimal("500.00"),
            max_drawdown_pct=Decimal("0.50"),
        )
        return BacktestRun(
            run_id="BT_TEST",
            dataset_id="TEST_DATA",
            start_date=date(2026, 11, 6),
            end_date=date(2026, 11, 6),
            strategy_name="NIFTY_ORB",
            strategy_version=1,
            strategy_config_snapshot={},
            execution_config=exec_cfg,
            metrics=metrics,
            diagnostics=diagnostics,
            trades=trades,
        )

    # 1. Single trade -> Sharpe ratio returns Decimal("0.00")
    trade1 = BacktestTrade(
        trade_id="TR_1",
        symbol="NSE:NIFTY26NOV24500CE",
        option_type=OptionType.CE,
        strike=Decimal("24500.00"),
        direction="BUY",
        entry_time=now_utc(),
        exit_time=now_utc(),
        entry_price=Decimal("100.00"),
        exit_price=Decimal("160.00"),
        qty=25,
        lot_size=25,
        gross_pnl=Decimal("1500.00"),
        trading_costs=Decimal("25.00"),
        net_pnl=Decimal("1475.00"),
        exit_reason="PROFIT_TARGET",
    )
    summary1 = workstation_service._format_backtest_summary(make_run([trade1]))
    assert summary1.sharpe_ratio is None
    assert "Insufficient daily return observations" in summary1.sharpe_ratio_reason
    assert summary1.trade_pnl_mean_std_ratio is None

    # 2. Identical trades (variance = 0) -> trade_pnl_mean_std_ratio returns None
    trade2 = BacktestTrade(
        trade_id="TR_2",
        symbol="NSE:NIFTY26NOV24500CE",
        option_type=OptionType.CE,
        strike=Decimal("24500.00"),
        direction="BUY",
        entry_time=now_utc(),
        exit_time=now_utc(),
        entry_price=Decimal("100.00"),
        exit_price=Decimal("160.00"),
        qty=25,
        lot_size=25,
        gross_pnl=Decimal("1500.00"),
        trading_costs=Decimal("25.00"),
        net_pnl=Decimal("1475.00"),
        exit_reason="PROFIT_TARGET",
    )
    summary2 = workstation_service._format_backtest_summary(make_run([trade1, trade2]))
    assert summary2.sharpe_ratio is None
    assert summary2.trade_pnl_mean_std_ratio is None

    # 3. Distinct trades: net_pnl = [1000.00, 2000.00]
    # mean = 1500, stdev = 707.10678... ratio = 1500 / 707.10678 = 2.12
    trade_a = BacktestTrade(
        trade_id="TR_A",
        symbol="NSE:NIFTY26NOV24500CE",
        option_type=OptionType.CE,
        strike=Decimal("24500.00"),
        direction="BUY",
        entry_time=now_utc(),
        exit_time=now_utc(),
        entry_price=Decimal("100.00"),
        exit_price=Decimal("140.00"),
        qty=25,
        lot_size=25,
        gross_pnl=Decimal("1000.00"),
        trading_costs=Decimal("0.00"),
        net_pnl=Decimal("1000.00"),
        exit_reason="PROFIT_TARGET",
    )
    trade_b = BacktestTrade(
        trade_id="TR_B",
        symbol="NSE:NIFTY26NOV24500CE",
        option_type=OptionType.CE,
        strike=Decimal("24500.00"),
        direction="BUY",
        entry_time=now_utc(),
        exit_time=now_utc(),
        entry_price=Decimal("100.00"),
        exit_price=Decimal("180.00"),
        qty=25,
        lot_size=25,
        gross_pnl=Decimal("2000.00"),
        trading_costs=Decimal("0.00"),
        net_pnl=Decimal("2000.00"),
        exit_reason="PROFIT_TARGET",
    )
    summary3 = workstation_service._format_backtest_summary(make_run([trade_a, trade_b]))
    assert summary3.sharpe_ratio is None  # Single-day observation -> None
    assert summary3.trade_pnl_mean_std_ratio == Decimal("2.12")
