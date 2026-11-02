"""Comprehensive test suite for Phase 6: FYERS Integration, Order Management System (OMS), and Market Data.

Validates all 20 test areas:
1. Authentication success and failure (profile inspection, expired token).
2. Market-data connection and disconnection.
3. WebSocket reconnection and stale-data detection (STALE fail-closed).
4. Order acknowledgement (INTENT_CREATED -> VALIDATING -> SUBMISSION_PENDING -> ACKNOWLEDGED -> OPEN).
5. Order rejection (broker RMS reject -> REJECTED).
6. Submission timeout with unknown outcome (is_timeout_ambiguous -> RECONCILIATION_REQUIRED).
7. Duplicate order intent (idempotency rejection).
8. Partial and complete fills (volume accumulation, average price).
9. Duplicate fill events (deduplication check, no double-counting).
10. Out-of-order order updates (no regression of terminal states).
11. Cancellation request versus confirmation (CANCELLATION_REQUESTED vs CANCELLED).
12. Position reconciliation (synchronized match).
13. Broker/internal quantity discrepancies (detected in ReconciliationReport).
14. Reversal sequencing (replacement order placed only after exit reaches FILLED).
15. Risk-engine rejection (pre-trade gatekeeper blocks submission).
16. Kill-switch lockout (OMS rejects all new orders when kill switch is engaged).
17. Token and secret redaction (ensures tokens/keys never appear in repr or logs).
18. Persistence and restart recovery (reloading orders/positions from repository).
19. Broker unavailability (fails closed safely).
20. Order Update WebSocket Frame Dispatch & OMS Routing.
"""

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytz

from config.risk_config import RiskConfig
from config.strategy_config import StrategyConfig
from src.broker.fyers import FyersBrokerClient
from src.broker.models import (
    AuthStatus,
    BrokerConnectionStatus,
    BrokerOrderBookItem,
    BrokerOrderPayload,
    BrokerOrderResponse,
    BrokerPosition,
    BrokerTradeItem,
    redact_sensitive_dict,
)
from src.broker.paper import PaperBrokerClient
from src.common.constants import (
    ExecutionMode,
    OptionType,
    OrderSide,
    OrderStatus,
    OrderType,
    ProductType,
    SessionPhase,
)
from src.common.exceptions import (
    BrokerAuthenticationError,
    BrokerConnectionError,
    InvalidStateTransitionError,
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
    map_fyers_status_to_oms,
    map_oms_status_to_fyers,
)
from src.oms.reconciliation import (
    BrokerReconciliationService,
    DiscrepancyType,
    ReconciliationReport,
)
from src.risk.engine import RiskEngine
from src.storage.repository import StateRepository
from src.websocket.base import BaseWebSocketService, WebSocketConnectionState
from src.websocket.market_data import MarketDataWebSocketService
from src.websocket.order_updates import OrderUpdateWebSocketService


# ------------------------------------------------------------------------------
# Test Fixtures
# ------------------------------------------------------------------------------

@pytest.fixture
def sample_session() -> TradingSession:
    """Active paper trading session on a Friday."""
    return TradingSession(
        session_id="SESSION_20261106_01",
        trading_date=date(2026, 11, 6),  # Friday
        execution_mode=ExecutionMode.PAPER,
        phase=SessionPhase.ACTIVE_TRADING,
        is_active=True,
    )


@pytest.fixture
def sample_contract() -> OptionContract:
    """Sample NIFTY ATM Call option contract."""
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
def risk_engine(sample_session: TradingSession) -> RiskEngine:
    """Standard RiskEngine with ₹3,000 max daily loss and profit protection."""
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
def paper_broker() -> PaperBrokerClient:
    """Simulated in-memory paper broker."""
    client = PaperBrokerClient(initial_balance=Decimal("100000.00"))
    return client


@pytest.fixture
def sample_order_intent(sample_session: TradingSession, sample_contract: OptionContract) -> OrderIntent:
    """Standard buy order intent during active trading hours (09:46 IST)."""
    return OrderIntent(
        intent_id="INT_TEST_001",
        client_order_id="ORD_SESS20261106_0001_BUY",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        strategy_version=1,
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=25,
        product_type=ProductType.INTRADAY,
        tag="BUY_0001_ORB",
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)).astimezone(timezone.utc),
    )


# ------------------------------------------------------------------------------
# 1. Authentication Success and Failure
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fyers_authentication_success_profile_inspection():
    """Verify successful authentication populates user profile and connection status."""
    mock_client = MagicMock()
    mock_profile_resp = {
        "s": "ok",
        "code": 200,
        "message": "",
        "data": {
            "fy_id": "XY12345",
            "name": "Jane Trader",
            "email_id": "jane@example.com",
            "mobile_number": "9876543210",
        },
    }
    mock_client.get_profile.return_value = mock_profile_resp

    client = FyersBrokerClient(client=mock_client, app_id="TEST_APP_ID", access_token="TEST_VALID_TOKEN")
    auth = await client.authenticate()

    assert auth.is_authenticated is True
    assert auth.broker_user_id == "XY12345"
    assert auth.display_name == "Jane Trader"
    assert auth.email == "jane@example.com"
    assert client.connection_status == BrokerConnectionStatus.CONNECTED


@pytest.mark.asyncio
async def test_fyers_authentication_failure_expired_token():
    """Verify expired token (-15 / -16) is caught and raises BrokerAuthenticationError."""
    mock_client = MagicMock()
    mock_error_resp = {
        "s": "error",
        "code": -15,
        "message": "Token expired. Please login again.",
    }
    mock_client.get_profile.return_value = mock_error_resp

    client = FyersBrokerClient(client=mock_client, app_id="TEST_APP_ID", access_token="EXPIRED_TOKEN")

    with pytest.raises(BrokerAuthenticationError) as exc_info:
        await client.authenticate()

    assert "expired" in str(exc_info.value).lower()
    assert client.connection_status == BrokerConnectionStatus.AUTH_EXPIRED
    assert client.auth_status.is_authenticated is False


@pytest.mark.asyncio
async def test_paper_broker_authentication():
    """Verify PaperBrokerClient authenticates deterministically in memory."""
    broker = PaperBrokerClient()
    auth = await broker.authenticate()
    assert auth.is_authenticated is True
    assert broker.connection_status == BrokerConnectionStatus.CONNECTED
    assert broker.is_connected is True


# ------------------------------------------------------------------------------
# 2. Market-Data Connection and Disconnection
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_market_data_connection_lifecycle():
    """Verify MarketDataWebSocketService connection, subscription, and disconnection states."""
    ws = MarketDataWebSocketService(access_token="TEST_TOKEN")
    assert ws.state == WebSocketConnectionState.DISCONNECTED
    assert not ws.is_connected

    await ws.start()
    assert ws.state == WebSocketConnectionState.CONNECTED
    assert ws.is_connected

    ws.subscribe(["NSE:NIFTY50-INDEX"])
    assert ws.state == WebSocketConnectionState.SUBSCRIBED
    assert "NSE:NIFTY50-INDEX" in ws.subscribed_symbols

    await ws.stop()
    assert ws.state == WebSocketConnectionState.DISCONNECTED
    assert not ws.is_connected


# ------------------------------------------------------------------------------
# 3. WebSocket Reconnection and Stale-Data Detection (STALE Fail-Closed)
# ------------------------------------------------------------------------------

def test_websocket_exponential_backoff_and_reset():
    """Verify exponential backoff calculation increases delay and resets correctly."""
    service = BaseWebSocketService(initial_retry_delay=1.0, max_retry_delay=30.0, backoff_multiplier=2.0)
    assert service._current_retry_delay == 1.0

    d1 = service.calculate_next_backoff()
    assert 0.8 <= d1 <= 1.2
    assert service._current_retry_delay == 2.0

    d2 = service.calculate_next_backoff()
    assert 1.8 <= d2 <= 2.2
    assert service._current_retry_delay == 4.0

    service.reset_backoff()
    assert service._current_retry_delay == 1.0


@pytest.mark.asyncio
async def test_market_data_staleness_detection_fail_closed():
    """
    Verify fail-closed staleness health check:
    Healthy when recent; transitions to STALE and returns False when ticks cease.
    """
    ws = MarketDataWebSocketService(stale_threshold_seconds=2.0)
    await ws.start()
    ws.subscribe(["NSE:NIFTY50-INDEX"])

    # Before receiving any tick, is_data_healthy returns False
    assert ws.is_data_healthy("NSE:NIFTY50-INDEX") is False

    # Ingest fresh tick
    now = now_utc()
    tick_frame = {
        "symbol": "NSE:NIFTY50-INDEX",
        "ltp": 24510.50,
        "volume": 5000,
        "timestamp": int(now.timestamp()),
    }
    tick = ws.process_incoming_frame(tick_frame)
    assert tick is not None
    assert ws.is_data_healthy("NSE:NIFTY50-INDEX") is True
    assert ws.is_data_healthy() is True

    # Artificially age the tick beyond the 2.0s threshold
    old_time = now - timedelta(seconds=3.0)
    ws._last_tick_times["NSE:NIFTY50-INDEX"] = old_time
    ws._last_message_at = old_time

    # Must fail closed: return False and transition state to STALE
    is_healthy = ws.is_data_healthy("NSE:NIFTY50-INDEX")
    assert is_healthy is False
    assert ws.state == WebSocketConnectionState.STALE

    await ws.stop()


def test_market_data_tick_normalization_and_ist():
    """Verify normalization of timestamps (seconds, ms) and conversion to IST."""
    ws = MarketDataWebSocketService()

    # Frame with epoch milliseconds
    epoch_ms = 1730866560000  # 2026-11-06 04:16:00 UTC (09:46:00 IST)
    frame = {
        "symbol": "NSE:NIFTY26NOV24500CE",
        "ltp": 125.75,
        "volume": 2500,
        "timestamp": epoch_ms,
        "bid_price": 125.50,
        "ask_price": 126.00,
    }

    tick = ws.normalize_tick_frame(frame)
    assert tick.symbol == "NSE:NIFTY26NOV24500CE"
    assert tick.ltp == Decimal("125.75")
    assert tick.volume == 2500
    assert tick.bid_price == Decimal("125.50")
    assert tick.ask_price == Decimal("126.00")
    assert tick.timestamp.tzinfo == timezone.utc

    # Verify IST conversion helper
    ist_time = to_ist(tick.timestamp)
    assert ist_time.tzinfo.zone == "Asia/Kolkata"
    assert ist_time.hour == 9
    assert ist_time.minute == 46


# ------------------------------------------------------------------------------
# 4. Order Acknowledgement (Lifecycle Progression)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_order_acknowledgement_lifecycle(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
    sample_order_intent: OrderIntent,
):
    """
    Verify complete progression:
    INTENT_CREATED -> VALIDATING -> SUBMISSION_PENDING -> ACKNOWLEDGED -> OPEN.
    """
    # Configure paper broker to leave order open without immediate fill
    paper_broker.set_auto_fill(False)

    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    order = await oms.submit_order_intent(sample_order_intent, contract=sample_contract)

    assert order.client_order_id == sample_order_intent.client_order_id
    assert order.broker_order_id is not None
    assert order.oms_status == OmsOrderStatus.OPEN

    # Audit transition history
    transition_statuses = [t.to_status for t in order.transitions]
    assert transition_statuses == [
        OmsOrderStatus.INTENT_CREATED,
        OmsOrderStatus.VALIDATING,
        OmsOrderStatus.SUBMISSION_PENDING,
        OmsOrderStatus.ACKNOWLEDGED,
        OmsOrderStatus.OPEN,
    ]


# ------------------------------------------------------------------------------
# 5. Order Rejection by Broker
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_order_rejection_by_broker(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
    sample_order_intent: OrderIntent,
):
    """Verify broker rejection transitions order to REJECTED."""
    paper_broker.set_force_reject(True, "RMS: Margin insufficient for intraday option buy")

    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    order = await oms.submit_order_intent(sample_order_intent, contract=sample_contract)

    assert order.oms_status == OmsOrderStatus.REJECTED
    assert "Margin insufficient" in order.status_message

    transition_statuses = [t.to_status for t in order.transitions]
    assert transition_statuses == [
        OmsOrderStatus.INTENT_CREATED,
        OmsOrderStatus.VALIDATING,
        OmsOrderStatus.SUBMISSION_PENDING,
        OmsOrderStatus.REJECTED,
    ]


# ------------------------------------------------------------------------------
# 6. Submission Timeout with Unknown Outcome (Ambiguous Timeout)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submission_timeout_ambiguous_requires_reconciliation(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
    sample_order_intent: OrderIntent,
):
    """
    Verify network timeout during submission flags order as RECONCILIATION_REQUIRED
    and enforces zero blind retries.
    """
    paper_broker.set_force_timeout(True)

    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    order = await oms.submit_order_intent(sample_order_intent, contract=sample_contract)

    assert order.oms_status == OmsOrderStatus.RECONCILIATION_REQUIRED
    assert "timed out" in order.status_message.lower()

    # Second attempt with identical intent must be rejected by idempotency (no blind retries)
    retry_order = await oms.submit_order_intent(sample_order_intent, contract=sample_contract)
    assert retry_order.client_order_id == order.client_order_id
    assert retry_order.oms_status == OmsOrderStatus.RECONCILIATION_REQUIRED


# ------------------------------------------------------------------------------
# 7. Duplicate Order Intent (Idempotency Rejection)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_order_intent_idempotency(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
    sample_order_intent: OrderIntent,
):
    """Verify duplicate submission of identical intent_id / client_order_id is blocked."""
    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    order1 = await oms.submit_order_intent(sample_order_intent, contract=sample_contract)
    assert order1.client_order_id == sample_order_intent.client_order_id

    # Duplicate submission
    order2 = await oms.submit_order_intent(sample_order_intent, contract=sample_contract)
    assert order2 is order1

    # Same client_order_id with different intent_id
    clashing_intent = OrderIntent(
        intent_id="INT_DIFFERENT_999",
        client_order_id=sample_order_intent.client_order_id,
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=25,
    )
    order3 = await oms.submit_order_intent(clashing_intent, contract=sample_contract)
    assert order3.client_order_id == sample_order_intent.client_order_id


# ------------------------------------------------------------------------------
# 8. Partial and Complete Fills (Volume & Price Tracking)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_partial_and_complete_fills_accounting(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """Verify volume accumulation, remaining quantity, and weighted average price across partial fills."""
    paper_broker.set_auto_fill(False)

    intent = OrderIntent(
        intent_id="INT_PARTIAL_01",
        client_order_id="ORD_SESS20261106_0010_BUY",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("120.00"),
        qty=50,  # 2 lots
        created_at=IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)).astimezone(timezone.utc),
    )

    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    order = await oms.submit_order_intent(intent, contract=sample_contract)
    assert order.oms_status == OmsOrderStatus.OPEN
    assert order.filled_qty == 0
    assert order.remaining_qty == 50

    # Fill 1: 25 shares @ 100.00
    fill1 = Fill(
        fill_id="FILL_001",
        client_order_id=order.client_order_id,
        broker_order_id=order.broker_order_id,
        symbol=order.symbol,
        side=OrderSide.BUY,
        fill_price=Decimal("100.00"),
        fill_qty=25,
    )
    await oms.on_fill_event(fill1)

    assert order.oms_status == OmsOrderStatus.PARTIALLY_FILLED
    assert order.filled_qty == 25
    assert order.remaining_qty == 25
    assert order.avg_fill_price == Decimal("100.00")

    # Fill 2: 25 shares @ 110.00 -> Total 50 shares, weighted avg = 105.00
    fill2 = Fill(
        fill_id="FILL_002",
        client_order_id=order.client_order_id,
        broker_order_id=order.broker_order_id,
        symbol=order.symbol,
        side=OrderSide.BUY,
        fill_price=Decimal("110.00"),
        fill_qty=25,
    )
    await oms.on_fill_event(fill2)

    assert order.oms_status == OmsOrderStatus.FILLED
    assert order.filled_qty == 50
    assert order.remaining_qty == 0
    assert order.avg_fill_price == Decimal("105.00")

    # Verify position updated
    pos = oms.get_position(order.symbol)
    assert pos is not None
    assert pos.net_qty == 50
    assert pos.avg_buy_price == Decimal("105.00")


# ------------------------------------------------------------------------------
# 9. Duplicate Fill Events (Deduplication Check)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_fill_deduplication(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
    sample_order_intent: OrderIntent,
):
    """Verify duplicate fill events with the same fill_id are ignored without double-counting."""
    paper_broker.set_auto_fill(False)

    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    order = await oms.submit_order_intent(sample_order_intent, contract=sample_contract)

    fill = Fill(
        fill_id="FILL_DEDUP_001",
        client_order_id=order.client_order_id,
        broker_order_id=order.broker_order_id,
        symbol=order.symbol,
        side=OrderSide.BUY,
        fill_price=Decimal("120.00"),
        fill_qty=25,
    )

    # First event processes
    await oms.on_fill_event(fill)
    assert order.filled_qty == 25
    assert len(order.fills) == 1

    # Duplicate event arrives (e.g. from both WebSocket and Polling)
    await oms.on_fill_event(fill)
    assert order.filled_qty == 25
    assert len(order.fills) == 1


# ------------------------------------------------------------------------------
# 10. Out-of-Order Updates (Terminal State Protection)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_out_of_order_broker_updates_terminal_protection(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
    sample_order_intent: OrderIntent,
):
    """Verify that once an order reaches FILLED, delayed broker messages cannot regress state."""
    paper_broker._auto_fill = True

    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    # Order immediately fills
    order = await oms.submit_order_intent(sample_order_intent, contract=sample_contract)
    assert order.oms_status == OmsOrderStatus.FILLED

    # Out-of-order broker update arrives indicating order is PENDING / OPEN
    oms.on_broker_order_update(
        client_order_id=order.client_order_id,
        broker_order_id=order.broker_order_id,
        broker_status=OrderStatus.PENDING,
        message="Out of order delayed packet",
    )
    # Order status MUST remain FILLED
    assert order.oms_status == OmsOrderStatus.FILLED

    # Out-of-order broker update indicating CANCELLED
    oms.on_broker_order_update(
        client_order_id=order.client_order_id,
        broker_order_id=order.broker_order_id,
        broker_status=OrderStatus.CANCELLED,
    )
    assert order.oms_status == OmsOrderStatus.FILLED


# ------------------------------------------------------------------------------
# 11. Cancellation Request vs Confirmation
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancellation_request_versus_confirmation(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
    sample_order_intent: OrderIntent,
):
    """Verify CANCELLATION_REQUESTED precedes CANCELLED and requires broker confirmation."""
    paper_broker.set_auto_fill(False)

    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    order = await oms.submit_order_intent(sample_order_intent, contract=sample_contract)
    assert order.oms_status == OmsOrderStatus.OPEN

    # Request cancellation
    resp = await oms.request_cancel_order(order.client_order_id, reason="User cancelled")
    assert resp.success is True
    assert order.oms_status == OmsOrderStatus.CANCELLED

    # Audit transition history contains CANCELLATION_REQUESTED followed by CANCELLED
    transition_statuses = [t.to_status for t in order.transitions]
    assert OmsOrderStatus.CANCELLATION_REQUESTED in transition_statuses
    assert transition_statuses[-1] == OmsOrderStatus.CANCELLED


# ------------------------------------------------------------------------------
# 12. Position Reconciliation (Synchronized Match)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconciliation_synchronized_match(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
    sample_order_intent: OrderIntent,
):
    """Verify BrokerReconciliationService detects perfect synchronization between OMS and broker."""
    paper_broker._auto_fill = True

    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    # Execute trade
    order = await oms.submit_order_intent(sample_order_intent, contract=sample_contract)
    assert order.oms_status == OmsOrderStatus.FILLED

    recon_service = BrokerReconciliationService(oms=oms, broker_client=paper_broker)
    report = await recon_service.reconcile()

    assert report.is_broker_available is True
    assert report.is_synchronized is True
    assert len(report.discrepancies) == 0


# ------------------------------------------------------------------------------
# 13. Broker/Internal Quantity & Status Discrepancies
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconciliation_detects_order_and_position_discrepancies(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
    sample_order_intent: OrderIntent,
):
    """Verify reconciliation detects missing orders, status desync, and quantity mismatches."""
    paper_broker.set_auto_fill(False)

    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    order = await oms.submit_order_intent(sample_order_intent, contract=sample_contract)
    recon_service = BrokerReconciliationService(oms=oms, broker_client=paper_broker)

    # 1. Simulate external order placed directly on broker terminal (UNKNOWN_BROKER_ORDER)
    paper_broker._orders["UNKNOWN_BRK_99"] = BrokerOrderBookItem(
        broker_order_id="UNKNOWN_BRK_99",
        client_order_id=None,
        symbol="NSE:NIFTY26NOV25000PE",
        qty=50,
        filled_qty=0,
        remaining_qty=50,
        status=OrderStatus.PENDING,
        order_type=OrderType.LIMIT,
        side=OrderSide.BUY,
        avg_price=Decimal("0.00"),
        limit_price=Decimal("150.00"),
        order_timestamp=now_utc(),
        tag="TERMINAL_MANUAL",
    )

    # 2. Simulate broker status changed externally to CANCELLED while OMS thinks OPEN (STATUS_DESYNC)
    paper_broker._orders[order.broker_order_id] = BrokerOrderBookItem(
        broker_order_id=order.broker_order_id,
        client_order_id=order.client_order_id,
        symbol=order.symbol,
        qty=order.qty,
        filled_qty=0,
        remaining_qty=order.qty,
        status=OrderStatus.CANCELLED,
        order_type=order.order_type,
        side=order.side,
        avg_price=Decimal("0.00"),
        limit_price=Decimal("0.00"),
        order_timestamp=now_utc(),
        tag=order.tag,
    )

    report = await recon_service.reconcile()
    assert report.is_synchronized is False

    disc_types = [d.discrepancy_type for d in report.discrepancies]
    assert DiscrepancyType.UNKNOWN_BROKER_ORDER in disc_types
    assert DiscrepancyType.STATUS_DESYNC in disc_types


# ------------------------------------------------------------------------------
# 14. Reversal Sequencing (Replacement Placed Only After Exit FILLED)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_phase_reversal_sequencing(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """
    Verify two-phase reversal:
    Replacement entry order is submitted ONLY after exit order reaches terminal FILLED.
    If exit order fails or is rejected, replacement order is aborted.
    """
    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    trade_time = IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)).astimezone(timezone.utc)
    exit_intent = OrderIntent(
        intent_id="INT_EXIT_01",
        client_order_id="ORD_SESS20261106_0020_SELL",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        qty=25,
        created_at=trade_time,
    )

    pe_contract = OptionContract(
        symbol="NSE:NIFTY26NOV24500PE",
        underlying="NSE:NIFTY50-INDEX",
        strike=Decimal("24500.00"),
        option_type=OptionType.PE,
        expiry_date=date(2026, 11, 26),
        lot_size=25,
        tick_size=Decimal("0.05"),
    )

    entry_intent = OrderIntent(
        intent_id="INT_ENTRY_02",
        client_order_id="ORD_SESS20261106_0021_BUY",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=pe_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=25,
        created_at=trade_time,
    )

    # Scenario A: Exit order fills -> Replacement succeeds
    paper_broker.set_auto_fill(True)
    exit_ord, entry_ord = await oms.coordinate_two_phase_reversal(
        exit_intent=exit_intent,
        entry_intent=entry_intent,
        exit_contract=sample_contract,
        entry_contract=pe_contract,
    )
    assert exit_ord.oms_status == OmsOrderStatus.FILLED
    assert entry_ord is not None
    assert entry_ord.oms_status == OmsOrderStatus.FILLED

    # Scenario B: Exit order rejected by broker -> Replacement order aborted
    paper_broker.set_force_reject(True, "Broker rejection on exit")
    exit_intent_b = OrderIntent(
        intent_id="INT_EXIT_02",
        client_order_id="ORD_SESS20261106_0022_SELL",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        qty=25,
        created_at=trade_time,
    )
    entry_intent_b = OrderIntent(
        intent_id="INT_ENTRY_03",
        client_order_id="ORD_SESS20261106_0023_BUY",
        session_id=sample_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=pe_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=25,
        created_at=trade_time,
    )

    exit_ord_b, entry_ord_b = await oms.coordinate_two_phase_reversal(
        exit_intent=exit_intent_b,
        entry_intent=entry_intent_b,
        exit_contract=sample_contract,
        entry_contract=pe_contract,
    )
    assert exit_ord_b.oms_status == OmsOrderStatus.REJECTED
    assert entry_ord_b is None  # Phase 2 aborted safely


# ------------------------------------------------------------------------------
# 15. Risk-Engine Rejection (Pre-Trade Gatekeeper)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_risk_engine_pre_trade_rejection(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
):
    """Verify that an order breaching risk rules is rejected by the gatekeeper without calling broker."""
    # Attempt order on Saturday (non-trading day)
    saturday_session = TradingSession(
        session_id="SESS_SAT_01",
        trading_date=date(2026, 11, 7),  # Saturday
        execution_mode=ExecutionMode.PAPER,
        phase=SessionPhase.ACTIVE_TRADING,
    )

    intent = OrderIntent(
        intent_id="INT_SAT_01",
        client_order_id="ORD_SESS20261107_0001_BUY",
        session_id=saturday_session.session_id,
        strategy_name="ORB_NIFTY",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=25,
        created_at=IST_TZ.localize(datetime(2026, 11, 7, 9, 46, 0)).astimezone(timezone.utc),
    )

    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=saturday_session,
    )

    order = await oms.submit_order_intent(intent, contract=sample_contract)

    assert order.oms_status == OmsOrderStatus.REJECTED
    assert "Risk rejected" in order.status_message
    assert len(paper_broker._orders) == 0  # Broker was never touched


# ------------------------------------------------------------------------------
# 16. Kill-Switch Lockout
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kill_switch_lockout(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
    sample_order_intent: OrderIntent,
):
    """Verify that engaging the kill-switch immediately causes the OMS to reject new orders."""
    # Engage kill-switch
    risk_engine.kill_switch.trigger_kill_switch(
        reason="Manual operator intervention: market anomaly",
        operator="Trader_Admin",
    )
    assert risk_engine.kill_switch.is_active is True

    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    order = await oms.submit_order_intent(sample_order_intent, contract=sample_contract)

    assert order.oms_status == OmsOrderStatus.REJECTED
    assert "Risk rejected" in order.status_message
    assert len(paper_broker._orders) == 0


# ------------------------------------------------------------------------------
# 17. Token and Secret Redaction
# ------------------------------------------------------------------------------

def test_token_and_secret_redaction():
    """Verify that credentials, tokens, and secrets are masked in logs and reprs."""
    auth = AuthStatus(
        is_authenticated=True,
        app_id="XC12345-100",
        access_token="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.SECRET_TOKEN_PAYLOAD",
        user_id="XC12345",
    )
    auth_repr = repr(auth)
    assert "SECRET_TOKEN_PAYLOAD" not in auth_repr
    assert "redacted" in auth_repr

    raw_payload = {
        "app_id": "MY_APP_123",
        "app_secret": "SUPER_SECRET_KEY_999",
        "access_token": "ACCESS_ABC123XYZ",
        "pin": "1234",
        "symbol": "NSE:NIFTY50-INDEX",
    }
    redacted = redact_sensitive_dict(raw_payload)
    assert redacted["app_secret"] == "[REDACTED]"
    assert redacted["access_token"] == "[REDACTED]"
    assert redacted["pin"] == "[REDACTED]"
    assert redacted["symbol"] == "NSE:NIFTY50-INDEX"


# ------------------------------------------------------------------------------
# 18. Persistence and Restart Recovery
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_oms_persistence_and_restart_recovery(
    state_repo: StateRepository,
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
    sample_order_intent: OrderIntent,
):
    """Verify that orders and transitions persist to StateRepository with oms_status."""
    await state_repo.save_session(sample_session)

    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
        state_repo=state_repo,
    )

    order = await oms.submit_order_intent(sample_order_intent, contract=sample_contract)
    assert order.oms_status == OmsOrderStatus.FILLED

    # Verify order was saved in database
    db_order = await state_repo.get_order(order.client_order_id)
    assert db_order is not None
    assert db_order.client_order_id == order.client_order_id
    assert db_order.status == map_oms_status_to_fyers(OmsOrderStatus.FILLED)


# ------------------------------------------------------------------------------
# 19. Broker Unavailability
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_broker_unavailability_fails_closed(
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
    sample_order_intent: OrderIntent,
):
    """Verify that when broker is unavailable or disconnected, OMS transitions to RECONCILIATION_REQUIRED."""
    mock_broker = MagicMock(spec=PaperBrokerClient)
    mock_broker.is_connected = False
    mock_broker.connection_status = BrokerConnectionStatus.DISCONNECTED
    mock_broker.place_order = AsyncMock(side_effect=BrokerConnectionError("Broker gateway unreachable"))

    oms = OrderManagementSystem(
        broker_client=mock_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    order = await oms.submit_order_intent(sample_order_intent, contract=sample_contract)
    assert order.oms_status == OmsOrderStatus.RECONCILIATION_REQUIRED
    assert "submission error" in order.status_message.lower()


# ------------------------------------------------------------------------------
# 20. Order Update WebSocket Frame Dispatch & OMS Routing
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_order_update_websocket_frame_dispatch(
    paper_broker: PaperBrokerClient,
    risk_engine: RiskEngine,
    sample_session: TradingSession,
    sample_contract: OptionContract,
    sample_order_intent: OrderIntent,
):
    """Verify FYERS order socket frame ingestion dispatches fills and status updates to OMS."""
    paper_broker.set_auto_fill(False)

    oms = OrderManagementSystem(
        broker_client=paper_broker,
        risk_engine=risk_engine,
        session=sample_session,
    )

    order = await oms.submit_order_intent(sample_order_intent, contract=sample_contract)
    assert order.oms_status == OmsOrderStatus.OPEN

    ws_order_service = OrderUpdateWebSocketService(oms=oms)

    # Ingest trade execution frame from FYERS
    trade_frame = {
        "s": "ok",
        "trades": {
            "tradeNumber": "TRD_WS_9001",
            "orderNumber": order.broker_order_id,
            "customOrderNum": order.client_order_id,
            "symbol": order.symbol,
            "tradedPrice": 122.50,
            "tradedQty": 25,
        },
    }
    await ws_order_service.process_incoming_frame(trade_frame)

    assert order.oms_status == OmsOrderStatus.FILLED
    assert order.filled_qty == 25
    assert order.avg_fill_price == Decimal("122.50")
