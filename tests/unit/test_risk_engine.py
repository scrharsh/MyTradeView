"""Comprehensive unit tests for the dedicated Risk Management Layer and Configuration-Driven Architecture."""

from datetime import date, datetime, time, timezone, timedelta
from decimal import Decimal
import pytest
import pytz

from config.risk_config import RiskConfig
from config.strategy_config import StrategyConfig
from src.common.constants import (
    ExecutionMode,
    KillSwitchState,
    OptionType,
    OrderSide,
    OrderStatus,
    OrderType,
    ProductType,
    ProfitTrailingMode,
    RiskRejectReason,
    RiskTriggerReason,
    SessionPhase,
    StrategyTemplateType,
)
from src.common.exceptions import ConfigurationError
from src.core.models import (
    BrokerOrder,
    Fill,
    OptionContract,
    OrderRequest,
    Position,
    TradingSession,
)
from src.core.strategy_template import (
    StrategyParameterDefinition,
    StrategyTemplate,
    VersionedStrategyConfig,
    get_nifty_orb_template,
    get_strategy_template,
)
from src.risk.circuit_breaker import DailyLossCircuitBreaker
from src.risk.engine import RiskEngine
from src.risk.kill_switch import KillSwitchCoordinator
from src.risk.pre_trade import PreTradeGatekeeper
from src.risk.profit_protection import ProfitProtectionEngine, ProfitProtectionStatus
from src.risk.tracker import MtmTracker

IST = pytz.timezone("Asia/Kolkata")


def make_ist_datetime(date_str: str, time_str: str) -> datetime:
    """Helper to construct timezone-aware UTC datetime from IST string."""
    dt_naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    dt_ist = IST.localize(dt_naive)
    return dt_ist.astimezone(timezone.utc)


@pytest.fixture
def sample_session() -> TradingSession:
    """Provide a standard active paper trading session on a Friday."""
    return TradingSession(
        session_id="SESS_RISK_01",
        trading_date=date(2026, 11, 6),  # Friday
        execution_mode=ExecutionMode.PAPER,
        phase=SessionPhase.ACTIVE_TRADING,
        is_active=True,
    )


@pytest.fixture
def sample_contract() -> OptionContract:
    """Provide a valid option contract with dynamic lot size."""
    return OptionContract(
        symbol="NSE:NIFTY26N0624500CE",
        underlying="NSE:NIFTY50-INDEX",
        strike=Decimal("24500.00"),
        option_type=OptionType.CE,
        expiry_date=date(2026, 11, 6),
        lot_size=25,
        tick_size=Decimal("0.05"),
    )


@pytest.fixture
def sample_order_request() -> OrderRequest:
    """Provide a valid 1-lot order request."""
    return OrderRequest(
        client_order_id="ORD_RISK_001",
        session_id="SESS_RISK_01",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=25,
        product_type=ProductType.INTRADAY,
        tag="TAG_001",
    )


@pytest.fixture
def risk_engine() -> RiskEngine:
    """Provide a standard RiskEngine instance configured with ₹3,000 limits."""
    risk_cfg = RiskConfig(
        max_daily_loss_limit=Decimal("3000.00"),
        profit_protection_trigger=Decimal("3000.00"),
        profit_protection_min_lock=Decimal("500.00"),
        profit_trailing_mode=ProfitTrailingMode.LOCK_AND_TRAIL,
        profit_trailing_step_profit=Decimal("1000.00"),
        profit_trailing_step_lock=Decimal("500.00"),
    )
    strat_cfg = StrategyConfig(
        underlying_symbol="NSE:NIFTY50-INDEX",
        orb_start_time=time(9, 16, 0),
        mandatory_exit_time=time(15, 10, 0),
        trading_days=["Monday", "Thursday", "Friday"],
    )
    return RiskEngine(
        session_id="SESS_RISK_01",
        risk_config=risk_cfg,
        strategy_config=strat_cfg,
        staleness_threshold_seconds=60,
    )


# ==============================================================================
# 1. Mark-to-Market Accounting & Precision
# ==============================================================================

def test_mtm_calculation_realized_and_unrealized():
    """Verify precision MTM calculation with realized, unrealized P&L and zero double-counting."""
    tracker = MtmTracker(session_id="SESS_01")

    # Ingest BUY fill: 25 qty @ 100.00
    fill_buy = Fill(
        client_order_id="ORD_BUY_01",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.BUY,
        fill_price=Decimal("100.00"),
        fill_qty=25,
        timestamp=datetime.now(timezone.utc),
    )
    tracker.on_fill(fill_buy)

    assert tracker.realized_pnl == Decimal("0.00")
    assert tracker.unrealized_pnl == Decimal("0.00")
    assert tracker.total_mtm == Decimal("0.00")
    assert tracker.open_positions_count == 1

    # Market price moves up to 120.00 -> Unrealized = (120 - 100) * 25 = +500.00
    tracker.update_market_price("NSE:NIFTY26N0624500CE", Decimal("120.00"))
    assert tracker.realized_pnl == Decimal("0.00")
    assert tracker.unrealized_pnl == Decimal("500.00")
    assert tracker.total_mtm == Decimal("500.00")
    assert tracker.peak_mtm == Decimal("500.00")
    assert tracker.current_drawdown == Decimal("0.00")

    # Sell full position @ 125.00 -> Realized = (125 - 100) * 25 = +625.00, Unrealized = 0
    fill_sell = Fill(
        client_order_id="ORD_SELL_01",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.SELL,
        fill_price=Decimal("125.00"),
        fill_qty=25,
        timestamp=datetime.now(timezone.utc),
    )
    tracker.on_fill(fill_sell)

    assert tracker.realized_pnl == Decimal("625.00")
    assert tracker.unrealized_pnl == Decimal("0.00")
    assert tracker.total_mtm == Decimal("625.00")
    assert tracker.open_positions_count == 0


def test_stale_and_invalid_market_data_rejection():
    """Verify that stale (>60s) or non-positive prices are rejected and flagged."""
    tracker = MtmTracker(session_id="SESS_01", staleness_threshold_seconds=60)

    # 1. Non-positive price
    assert not tracker.update_market_price("NSE:NIFTY", Decimal("0.00"))
    assert not tracker.update_market_price("NSE:NIFTY", Decimal("-10.50"))

    # 2. Fresh price
    now = datetime.now(timezone.utc)
    assert tracker.update_market_price("NSE:NIFTY", Decimal("24500.00"), timestamp=now)
    assert not tracker.is_data_stale

    # 3. Stale price (75 seconds old)
    old_time = now - timedelta(seconds=75)
    assert not tracker.update_market_price("NSE:NIFTY", Decimal("24510.00"), timestamp=old_time)
    assert tracker.is_data_stale


# ==============================================================================
# 2. Daily Loss Circuit Breaker & Duplicate Suppression
# ==============================================================================

def test_daily_loss_trigger_activation_and_closure_tracking():
    """Verify daily loss trigger (-₹3,000) activation, closure request, and confirmation."""
    breaker = DailyLossCircuitBreaker(max_daily_loss=Decimal("3000.00"))

    pos = Position(session_id="SESS_01", symbol="NSE:NIFTY26N0624500CE", net_qty=25, is_open=True)

    # Within limits: MTM = -2500
    assert not breaker.evaluate(Decimal("-2500.00"), open_positions=[pos])
    assert breaker.can_trade
    assert not breaker.is_locked_out

    # Breach threshold: MTM = -3050.00
    breached = breaker.evaluate(Decimal("-3050.00"), open_positions=[pos])
    assert breached
    assert breaker.is_locked_out
    assert not breaker.can_trade
    assert breaker.trigger_mtm == Decimal("-3050.00")

    # Request closure: first time returns symbol
    to_close = breaker.request_closure()
    assert to_close == ["NSE:NIFTY26N0624500CE"]
    assert breaker.closure_requested
    assert not breaker.closure_confirmed

    # Re-evaluating subsequent market tick must NOT fire another breach
    assert not breaker.evaluate(Decimal("-3100.00"), open_positions=[pos])

    # Request closure again while pending: duplicate request suppressed!
    duplicate_request = breaker.request_closure()
    assert duplicate_request == []

    # Confirm closure
    all_closed = breaker.confirm_position_closed("NSE:NIFTY26N0624500CE")
    assert all_closed
    assert breaker.closure_confirmed
    assert breaker.is_locked_out  # Session remains locked out for day!


# ==============================================================================
# 3. Profit-Protection Lock and Trailing
# ==============================================================================

def test_profit_protection_lock_and_trail_mechanics():
    """Verify profit protection trigger (+₹3,000) locks floor (+₹500) and step-trails."""
    engine = ProfitProtectionEngine(
        trigger_profit=Decimal("3000.00"),
        min_lock=Decimal("500.00"),
        step_profit=Decimal("1000.00"),
        step_lock=Decimal("500.00"),
        mode=ProfitTrailingMode.LOCK_AND_TRAIL,
    )

    # 1. Under trigger: MTM = 2800 -> Inactive
    assert not engine.evaluate(Decimal("2800.00"))
    assert engine.status == ProfitProtectionStatus.INACTIVE

    # 2. Reaches +₹3,200 -> Activated, floor locked at ₹500
    assert not engine.evaluate(Decimal("3200.00"))
    assert engine.status == ProfitProtectionStatus.ACTIVATED
    assert engine.current_floor == Decimal("500.00")

    # 3. Advances to +₹4,200 (+₹1,200 above trigger -> 1 step) -> Floor trails to ₹1,000
    assert not engine.evaluate(Decimal("4200.00"))
    assert engine.step_count == 1
    assert engine.current_floor == Decimal("1000.00")

    # 4. Pullback to ₹950 (<= ₹1,000 floor) -> Exit triggered
    exit_needed = engine.evaluate(Decimal("950.00"))
    assert exit_needed
    assert engine.status == ProfitProtectionStatus.EXIT_REQUESTED

    # 5. Repeated ticks while in EXIT_REQUESTED must NOT emit duplicate exits
    assert not engine.evaluate(Decimal("920.00"))
    assert not engine.evaluate(Decimal("900.00"))

    # 6. Confirm exit
    engine.confirm_exit()
    assert engine.status == ProfitProtectionStatus.EXIT_CONFIRMED


def test_profit_protection_default_lock_minimum_only():
    """Verify default verified StockMock baseline: lock minimum ₹500 floor without unconfirmed trailing."""
    engine = ProfitProtectionEngine(
        trigger_profit=Decimal("3000.00"),
        min_lock=Decimal("500.00"),
        mode=ProfitTrailingMode.LOCK_MINIMUM_ONLY,
    )

    # 1. Crosses ₹3,000 -> Floor locked at ₹500
    engine.evaluate(Decimal("3500.00"))
    assert engine.is_activated
    assert engine.current_floor == Decimal("500.00")

    # 2. Profit surges to ₹6,000 -> In LOCK_MINIMUM_ONLY, floor remains strictly at ₹500 (no invented trailing)
    engine.evaluate(Decimal("6000.00"))
    assert engine.current_floor == Decimal("500.00")
    assert engine.step_count == 0

    # 3. MTM drops to ₹600 -> Still above ₹500 floor, no exit
    assert not engine.evaluate(Decimal("600.00"))
    assert engine.status == ProfitProtectionStatus.ACTIVATED

    # 4. MTM drops to ₹490 (<= ₹500) -> Exit triggered
    assert engine.evaluate(Decimal("490.00"))
    assert engine.status == ProfitProtectionStatus.EXIT_REQUESTED


# ==============================================================================
# 4. Pre-Trade Gatekeeper Multi-Point Validations
# ==============================================================================

def test_pre_trade_gatekeeper_mode_and_idempotency(sample_order_request: OrderRequest, sample_session: TradingSession, sample_contract: OptionContract):
    """Verify gatekeeper blocks live mode without auth and prevents duplicate client order IDs."""
    gate = PreTradeGatekeeper()

    # 1. Reject LIVE execution mode in paper environment
    res_live = gate.validate_order_request(
        sample_order_request,
        execution_mode=ExecutionMode.LIVE,
        session=sample_session,
        contract=sample_contract,
    )
    assert not res_live.is_valid
    assert res_live.reason_code == RiskRejectReason.EXECUTION_MODE_DISABLED

    # 2. Valid PAPER order accepted
    eval_time = make_ist_datetime("2026-11-06", "10:00:00")
    res_valid = gate.validate_order_request(
        sample_order_request,
        execution_mode=ExecutionMode.PAPER,
        session=sample_session,
        contract=sample_contract,
        evaluation_time=eval_time,
    )
    assert res_valid.is_valid

    # 3. Duplicate order submission rejected
    res_dup = gate.validate_order_request(
        sample_order_request,
        execution_mode=ExecutionMode.PAPER,
        session=sample_session,
        contract=sample_contract,
        evaluation_time=eval_time,
    )
    assert not res_dup.is_valid
    assert res_dup.reason_code == RiskRejectReason.DUPLICATE_ORDER


def test_pre_trade_gatekeeper_risk_lockouts(sample_order_request: OrderRequest, sample_session: TradingSession, sample_contract: OptionContract):
    """Verify gatekeeper blocks new entries during kill-switch or profit-protection lockouts."""
    gate = PreTradeGatekeeper()
    eval_time = make_ist_datetime("2026-11-06", "10:00:00")

    # Blocked by active kill-switch / daily loss lockout
    res_ks = gate.validate_order_request(
        sample_order_request,
        execution_mode=ExecutionMode.PAPER,
        session=sample_session,
        contract=sample_contract,
        is_kill_switch_active=True,
        evaluation_time=eval_time,
    )
    assert not res_ks.is_valid
    assert res_ks.reason_code == RiskRejectReason.KILL_SWITCH_ACTIVE

    # Blocked by active profit protection lockout
    res_pp = gate.validate_order_request(
        sample_order_request,
        execution_mode=ExecutionMode.PAPER,
        session=sample_session,
        contract=sample_contract,
        is_profit_protection_locked=True,
        evaluation_time=eval_time,
    )
    assert not res_pp.is_valid
    assert res_pp.reason_code == RiskRejectReason.PROFIT_PROTECTION_LOCKED


def test_pre_trade_gatekeeper_calendar_and_session_checks(sample_order_request: OrderRequest, sample_session: TradingSession, sample_contract: OptionContract):
    """Verify gatekeeper enforces allowed trading days, session hours, and 15:10 IST cutoff."""
    gate = PreTradeGatekeeper(allowed_trading_days=["Monday", "Thursday", "Friday"])

    # 1. Non-trading day (Tuesday: 2026-11-03)
    tue_time = make_ist_datetime("2026-11-03", "10:00:00")
    res_tue = gate.validate_order_request(
        sample_order_request,
        execution_mode=ExecutionMode.PAPER,
        session=sample_session,
        contract=sample_contract,
        evaluation_time=tue_time,
    )
    assert not res_tue.is_valid
    assert res_tue.reason_code == RiskRejectReason.NON_TRADING_DAY

    # 2. Before ORB start (09:10 IST)
    early_time = make_ist_datetime("2026-11-06", "09:10:00")
    res_early = gate.validate_order_request(
        sample_order_request,
        execution_mode=ExecutionMode.PAPER,
        session=sample_session,
        contract=sample_contract,
        evaluation_time=early_time,
    )
    assert not res_early.is_valid
    assert res_early.reason_code == RiskRejectReason.OUTSIDE_TRADING_HOURS

    # 3. At or after mandatory square-off boundary (15:10:00 IST)
    late_time = make_ist_datetime("2026-11-06", "15:10:00")
    res_late = gate.validate_order_request(
        sample_order_request,
        execution_mode=ExecutionMode.PAPER,
        session=sample_session,
        contract=sample_contract,
        evaluation_time=late_time,
    )
    assert not res_late.is_valid
    assert res_late.reason_code == RiskRejectReason.MANDATORY_EXIT_PASSED


def test_pre_trade_gatekeeper_contract_and_quantity_compliance(sample_session: TradingSession, sample_contract: OptionContract):
    """Verify gatekeeper validates dynamic lot size and non-conforming quantities."""
    gate = PreTradeGatekeeper()
    eval_time = make_ist_datetime("2026-11-06", "10:00:00")

    # Contract lot size is 25. Order qty = 30 (not a multiple of 25)
    bad_qty_order = OrderRequest(
        client_order_id="ORD_BAD_QTY",
        session_id="SESS_01",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=30,
        tag="TAG_BAD",
    )
    res_qty = gate.validate_order_request(
        bad_qty_order,
        execution_mode=ExecutionMode.PAPER,
        session=sample_session,
        contract=sample_contract,
        evaluation_time=eval_time,
    )
    assert not res_qty.is_valid
    assert res_qty.reason_code == RiskRejectReason.INVALID_ORDER_QTY

    # Contract None
    res_no_contract = gate.validate_order_request(
        bad_qty_order,
        execution_mode=ExecutionMode.PAPER,
        session=sample_session,
        contract=None,
        evaluation_time=eval_time,
    )
    assert not res_no_contract.is_valid
    assert res_no_contract.reason_code == RiskRejectReason.INVALID_CONTRACT


def test_pre_trade_gatekeeper_conflicting_order_and_position_states(sample_order_request: OrderRequest, sample_session: TradingSession, sample_contract: OptionContract):
    """Verify gatekeeper rejects entry when an order is in-flight or opposing position is held."""
    gate = PreTradeGatekeeper()
    eval_time = make_ist_datetime("2026-11-06", "10:00:00")

    # 1. In-flight pending order
    pending_order = BrokerOrder(
        client_order_id="ORD_PENDING_01",
        session_id="SESS_01",
        symbol=sample_contract.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        product_type=ProductType.INTRADAY,
        qty=25,
        remaining_qty=25,
        status=OrderStatus.TRANSIT,
        tag="PENDING",
    )
    res_in_flight = gate.validate_order_request(
        sample_order_request,
        execution_mode=ExecutionMode.PAPER,
        session=sample_session,
        contract=sample_contract,
        pending_orders=[pending_order],
        evaluation_time=eval_time,
    )
    assert not res_in_flight.is_valid
    assert res_in_flight.reason_code == RiskRejectReason.CONFLICTING_ORDER_STATE

    # 2. Conflicting open opposing position (Holding PE while attempting to buy CE)
    opposing_pos = Position(
        session_id="SESS_01",
        symbol="NSE:NIFTY26N0624500PE",
        net_qty=25,
        is_open=True,
    )
    res_opposing = gate.validate_order_request(
        sample_order_request,
        execution_mode=ExecutionMode.PAPER,
        session=sample_session,
        contract=sample_contract,
        current_position=opposing_pos,
        evaluation_time=eval_time,
    )
    assert not res_opposing.is_valid
    assert res_opposing.reason_code == RiskRejectReason.CONFLICTING_ORDER_STATE


# ==============================================================================
# 5. Kill-Switch Coordinator State Machine
# ==============================================================================

def test_kill_switch_coordinator_state_lifecycle():
    """Verify kill switch transitions: NORMAL -> TRIGGERED -> EXIT_REQUESTED -> LOCKED_OUT."""
    coord = KillSwitchCoordinator(session_id="SESS_01")
    assert coord.state == KillSwitchState.NORMAL
    assert coord.can_trade

    pos = Position(session_id="SESS_01", symbol="NSE:NIFTY26N0624500CE", net_qty=25, is_open=True)
    pending_ord = BrokerOrder(
        client_order_id="ORD_PENDING",
        session_id="SESS_01",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        product_type=ProductType.INTRADAY,
        qty=25,
        remaining_qty=25,
        status=OrderStatus.PENDING,
        tag="TAG",
    )

    # 1. Trigger kill-switch
    triggered = coord.trigger(RiskTriggerReason.DAILY_LOSS_LIMIT_BREACHED)
    assert triggered
    assert coord.state == KillSwitchState.TRIGGERED
    assert not coord.can_trade

    # 2. Coordinate emergency actions (cancel orders and flatten positions)
    cancels, closures = coord.coordinate_emergency_actions([pos], [pending_ord])
    assert cancels == ["ORD_PENDING"]
    assert closures == ["NSE:NIFTY26N0624500CE"]
    assert coord.state == KillSwitchState.EXIT_REQUESTED

    # 3. Confirm order cancellation
    coord.confirm_cancellation("ORD_PENDING")
    assert coord.state == KillSwitchState.EXIT_REQUESTED  # Position closure still pending

    # 4. Confirm position closure -> Transitions to LOCKED_OUT
    coord.confirm_position_closed("NSE:NIFTY26N0624500CE")
    assert coord.state == KillSwitchState.LOCKED_OUT
    assert not coord.can_trade
    assert coord.locked_out_at is not None

    # Audit log verification
    assert len(coord.audit_log) >= 4


# ==============================================================================
# 6. Unified RiskEngine Integration & Reversal Protection
# ==============================================================================

def test_risk_engine_integration_and_reversal_interaction(risk_engine: RiskEngine, sample_session: TradingSession, sample_contract: OptionContract):
    """Verify integrated RiskEngine blocks new entries during daily loss breach even during reversal."""
    engine = risk_engine
    eval_time = make_ist_datetime("2026-11-06", "10:30:00")

    # Ingest position with severe loss (-₹3,200)
    pos = Position(
        session_id="SESS_RISK_01",
        symbol=sample_contract.symbol,
        net_qty=25,
        buy_qty=25,
        avg_buy_price=Decimal("200.00"),
        current_ltp=Decimal("72.00"),  # Loss = (72 - 200) * 25 = -3200
        unrealized_pnl=Decimal("-3200.00"),
        is_open=True,
    )
    engine.tracker.register_position(pos)

    # Price update triggers circuit breaker
    snapshot = engine.on_market_price_update(sample_contract.symbol, Decimal("72.00"), timestamp=eval_time, open_positions=[pos])
    assert snapshot.total_mtm == Decimal("-3200.00")
    assert snapshot.is_kill_switch_active
    assert not engine.can_trade

    # Reversal order attempt during lockout MUST be rejected by pre-trade gatekeeper
    reversal_order = OrderRequest(
        client_order_id="ORD_REVERSAL_PE",
        session_id="SESS_RISK_01",
        symbol="NSE:NIFTY26N0624500PE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=25,
        tag="REVERSAL",
    )
    res = engine.validate_order(
        reversal_order,
        execution_mode=ExecutionMode.PAPER,
        session=sample_session,
        contract=sample_contract,
        evaluation_time=eval_time,
    )
    assert not res.is_valid
    assert res.reason_code == RiskRejectReason.KILL_SWITCH_ACTIVE


# ==============================================================================
# 7. Configuration-Driven Strategy Architecture & Versioning
# ==============================================================================

def test_strategy_template_validation_and_unsupported_params():
    """Verify StrategyTemplate validation accepts valid parameters and rejects unsupported keys."""
    template = get_nifty_orb_template()
    assert template.template_type == StrategyTemplateType.NIFTY_ORB

    # 1. Valid parameters pass
    valid_params = {
        "underlying_symbol": "NSE:NIFTY50-INDEX",
        "orb_start_time": "09:16:00",
        "orb_end_time": "09:45:00",
        "mandatory_exit_time": "15:10:00",
        "trading_days": ["Monday", "Thursday", "Friday"],
        "lots": 1,
    }
    validated = template.validate_parameters(valid_params)
    assert validated["underlying_symbol"] == "NSE:NIFTY50-INDEX"
    assert validated["lots"] == 1

    # 2. Unsupported arbitrary parameter raises ConfigurationError
    invalid_params = dict(valid_params)
    invalid_params["arbitrary_secret_multiplier"] = 10  # Not in template
    with pytest.raises(ConfigurationError) as exc_info:
        template.validate_parameters(invalid_params)
    assert "Unsupported parameter(s)" in str(exc_info.value)


def test_versioned_strategy_config_progression():
    """Verify versioned strategy configuration snapshotting and immutable version progression."""
    v1 = VersionedStrategyConfig(
        name="Production NIFTY ORB Conservative",
        template_type=StrategyTemplateType.NIFTY_ORB,
        version=1,
        underlying_symbol="NSE:NIFTY50-INDEX",
        parameters={"lots": 1, "max_daily_loss_limit": "3000.00"},
    )
    assert v1.version == 1

    # Increment to version 2 with modified parameter
    v2 = v1.create_next_version({"lots": 2})
    assert v2.version == 2
    assert v2.name == v1.name
    assert v2.parameters["lots"] == 2
    assert v2.parameters["max_daily_loss_limit"] == "3000.00"
    assert v1.version == 1  # v1 remains immutable


def test_mtm_accounting_partial_fills_and_multiple_trades():
    """Verify precision MTM accounting with partial fills, partial exits, and successive trades."""
    tracker = MtmTracker(session_id="SESS_PARTIAL")

    # 1. Partial fill 1: Buy 15 @ 100
    fill1 = Fill(
        client_order_id="BUY_P1",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.BUY,
        fill_price=Decimal("100.00"),
        fill_qty=15,
        timestamp=datetime.now(timezone.utc),
    )
    tracker.on_fill(fill1)
    assert tracker.open_positions_count == 1
    assert tracker.total_exposure_quantity == 15

    # 2. Partial fill 2: Buy 10 @ 102 -> Total 25 @ avg 100.80
    fill2 = Fill(
        client_order_id="BUY_P2",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.BUY,
        fill_price=Decimal("102.00"),
        fill_qty=10,
        timestamp=datetime.now(timezone.utc),
    )
    tracker.on_fill(fill2)
    assert tracker.total_exposure_quantity == 25
    pos = tracker._positions["NSE:NIFTY26N0624500CE"]
    assert pos.avg_buy_price == Decimal("100.8000")

    # 3. Partial exit: Sell 10 @ 110.00 -> Realized P&L = (110 - 100.80) * 10 = +92.00
    fill_exit1 = Fill(
        client_order_id="SELL_P1",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.SELL,
        fill_price=Decimal("110.00"),
        fill_qty=10,
        timestamp=datetime.now(timezone.utc),
    )
    tracker.on_fill(fill_exit1)
    assert tracker.realized_pnl == Decimal("92.00")
    assert pos.net_qty == 15
    assert pos.is_open

    # 4. Price update at 115.00 -> Unrealized = (115 - 100.80) * 15 = +213.00
    tracker.update_market_price("NSE:NIFTY26N0624500CE", Decimal("115.00"))
    assert tracker.unrealized_pnl == Decimal("213.00")
    assert tracker.total_mtm == Decimal("305.00")  # 92 + 213 = 305.00

    # 5. Full exit of remaining 15 @ 120.00 -> Total realized = 92 + (120 - 100.80) * 15 = 92 + 288 = 380.00
    fill_exit2 = Fill(
        client_order_id="SELL_P2",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.SELL,
        fill_price=Decimal("120.00"),
        fill_qty=15,
        timestamp=datetime.now(timezone.utc),
    )
    tracker.on_fill(fill_exit2)
    assert pos.net_qty == 0
    assert not pos.is_open
    assert tracker.open_positions_count == 0
    assert tracker.realized_pnl == Decimal("380.00")
    assert tracker.unrealized_pnl == Decimal("0.00")
    assert tracker.total_mtm == Decimal("380.00")

    # 6. Successive trade 2 in same session for same contract: Buy 25 @ 130, Sell 25 @ 140
    # Must NOT corrupt previous trade's average buy price
    fill_t2_buy = Fill(
        client_order_id="BUY_T2",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.BUY,
        fill_price=Decimal("130.00"),
        fill_qty=25,
        timestamp=datetime.now(timezone.utc),
    )
    tracker.on_fill(fill_t2_buy)
    pos2 = tracker._positions["NSE:NIFTY26N0624500CE"]
    assert pos2.avg_buy_price == Decimal("130.0000")
    assert pos2.net_qty == 25

    fill_t2_sell = Fill(
        client_order_id="SELL_T2",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.SELL,
        fill_price=Decimal("140.00"),
        fill_qty=25,
        timestamp=datetime.now(timezone.utc),
    )
    tracker.on_fill(fill_t2_sell)
    assert not pos2.is_open
    # Total Realized = 380 + (10 * 25) = 380 + 250 = 630.00
    assert tracker.realized_pnl == Decimal("630.00")
    assert tracker.total_mtm == Decimal("630.00")


def test_kill_switch_action_failure_handling():
    """Verify that a rejected exit order does NOT transition to LOCKED_OUT, preventing false fill assumptions."""
    coord = KillSwitchCoordinator(session_id="SESS_FAIL")
    pos = Position(session_id="SESS_FAIL", symbol="NSE:NIFTY26N0624500CE", net_qty=25, is_open=True)

    coord.trigger(RiskTriggerReason.DAILY_LOSS_LIMIT_BREACHED)
    assert coord.state == KillSwitchState.TRIGGERED

    _, closures = coord.coordinate_emergency_actions([pos], [])
    assert closures == ["NSE:NIFTY26N0624500CE"]
    assert coord.state == KillSwitchState.EXIT_REQUESTED

    # Broker rejects the exit order (e.g. RMS circuit limit or API timeout)
    coord.record_action_failure("NSE:NIFTY26N0624500CE", "POSITION_EXIT", "Broker RMS rejected order: Outside circuit limits")

    # State MUST remain EXIT_REQUESTED, NOT LOCKED_OUT!
    assert coord.state == KillSwitchState.EXIT_REQUESTED
    assert not coord.can_trade
    assert coord.locked_out_at is None
    assert any(log["action"] == "ACTION_FAILED" for log in coord.audit_log)

