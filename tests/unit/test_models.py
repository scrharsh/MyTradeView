"""Unit tests for domain models with Decimal monetary precision."""

from datetime import date, datetime, timezone
from decimal import Decimal
import pytest
from pydantic import ValidationError

from src.common.constants import (
    OptionType,
    OrderSide,
    OrderStatus,
    OrderType,
    ProductType,
    RiskTriggerReason,
)
from src.core.models import (
    Candle,
    DailyRiskState,
    Fill,
    Instrument,
    MarketTick,
    OpeningRange,
    OptionContract,
    Position,
    ProfitProtectionState,
)


def test_monetary_precision_arithmetic():
    """Verify that Decimal avoids floating-point precision hazards (e.g. 0.1 + 0.2 != 0.3)."""
    # Standard IEEE-754 binary floating-point issue:
    assert (0.1 + 0.2) != 0.3

    # Exact Decimal behavior required by our models:
    dec_a = Decimal("0.10")
    dec_b = Decimal("0.20")
    assert (dec_a + dec_b) == Decimal("0.30")


def test_market_tick_validation():
    """Verify tick validation and UTC conversion using Decimal LTP."""
    dt = datetime(2026, 11, 2, 9, 30, 0, tzinfo=timezone.utc)
    tick = MarketTick(
        symbol="NSE:NIFTY50-INDEX",
        ltp=Decimal("24500.50"),
        volume=100,
        timestamp=dt,
        bid_price=Decimal("24500.40"),
        ask_price=Decimal("24500.60"),
    )
    assert tick.ltp == Decimal("24500.50")
    assert isinstance(tick.ltp, Decimal)
    assert tick.timestamp.tzinfo is not None

    with pytest.raises(ValidationError):
        # Non-positive price must fail
        MarketTick(symbol="NSE:NIFTY50-INDEX", ltp=Decimal("-10.00"), timestamp=dt)


def test_candle_bounds_validation():
    """Verify candle bar validation (High >= Open, Close, Low and Low <= Open, Close, High)."""
    dt = datetime(2026, 11, 2, 9, 30, 0, tzinfo=timezone.utc)
    candle = Candle(
        symbol="NSE:NIFTY50-INDEX",
        timestamp=dt,
        open=Decimal("24500.00"),
        high=Decimal("24550.00"),
        low=Decimal("24490.00"),
        close=Decimal("24530.00"),
        volume=1500,
    )
    assert candle.high >= candle.low

    with pytest.raises(ValidationError):
        # Invalid bar where low > high
        Candle(
            symbol="NSE:NIFTY50-INDEX",
            timestamp=dt,
            open=Decimal("24500.00"),
            high=Decimal("24400.00"),
            low=Decimal("24550.00"),
            close=Decimal("24450.00"),
            volume=100,
        )

    with pytest.raises(ValidationError):
        # Invalid bar where close > high
        Candle(
            symbol="NSE:NIFTY50-INDEX",
            timestamp=dt,
            open=Decimal("24500.00"),
            high=Decimal("24520.00"),
            low=Decimal("24480.00"),
            close=Decimal("24550.00"),
            volume=100,
        )


def test_opening_range_accumulation():
    """Verify opening range high/low accumulation and breakout detection using Decimal."""
    dt = datetime(2026, 11, 2, 9, 16, 0, tzinfo=timezone.utc)
    orb = OpeningRange(session_date=date(2026, 11, 2))

    c1 = Candle(
        symbol="NSE:NIFTY50-INDEX",
        timestamp=dt,
        open=Decimal("24500.00"),
        high=Decimal("24520.00"),
        low=Decimal("24480.00"),
        close=Decimal("24510.00"),
        volume=100,
    )
    c2 = Candle(
        symbol="NSE:NIFTY50-INDEX",
        timestamp=dt,
        open=Decimal("24510.00"),
        high=Decimal("24560.00"),
        low=Decimal("24490.00"),
        close=Decimal("24540.00"),
        volume=100,
    )
    c3 = Candle(
        symbol="NSE:NIFTY50-INDEX",
        timestamp=dt,
        open=Decimal("24540.00"),
        high=Decimal("24550.00"),
        low=Decimal("24460.00"),
        close=Decimal("24470.00"),
        volume=100,
    )

    orb.update_from_candle(c1)
    orb.update_from_candle(c2)
    orb.update_from_candle(c3)

    assert orb.high == Decimal("24560.00")
    assert orb.low == Decimal("24460.00")
    assert orb.candle_count == 3
    assert not orb.is_established

    orb.finalize()
    assert orb.is_established
    assert orb.is_broken_above(Decimal("24560.05"))
    assert not orb.is_broken_above(Decimal("24560.00"))
    assert orb.is_broken_below(Decimal("24459.95"))
    assert not orb.is_broken_below(Decimal("24460.00"))


def test_position_pnl_lifecycle_decimal():
    """Verify position net quantity, average prices, and realized/unrealized P&L in exact Decimal."""
    dt = datetime(2026, 11, 2, 9, 45, 0, tzinfo=timezone.utc)
    pos = Position(
        session_id="SESS_01",
        symbol="NSE:NIFTY26N0624500CE",
        product_type=ProductType.INTRADAY,
    )

    # Buy 1 lot (25 qty) @ 100.25
    fill1 = Fill(
        client_order_id="ORD_01",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.BUY,
        fill_price=Decimal("100.25"),
        fill_qty=25,
        timestamp=dt,
    )
    pos.apply_fill(fill1)
    assert pos.is_open
    assert pos.net_qty == 25
    assert pos.avg_buy_price == Decimal("100.25")
    assert pos.realized_pnl == Decimal("0.00")

    # LTP moves to 120.75
    pos.update_ltp(Decimal("120.75"))
    assert pos.unrealized_pnl == (Decimal("120.75") - Decimal("100.25")) * Decimal(25)  # 20.50 * 25 = 512.50
    assert pos.total_pnl == Decimal("512.50")

    # Sell 1 lot (25 qty) @ 135.50 (Close position)
    fill2 = Fill(
        client_order_id="ORD_02",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.SELL,
        fill_price=Decimal("135.50"),
        fill_qty=25,
        timestamp=dt,
    )
    pos.apply_fill(fill2)
    assert not pos.is_open
    assert pos.net_qty == 0
    # Realized PnL: (135.50 - 100.25) * 25 = 35.25 * 25 = 881.25
    assert pos.realized_pnl == Decimal("881.25")
    assert pos.unrealized_pnl == Decimal("0.00")
    assert pos.total_pnl == Decimal("881.25")


def test_daily_risk_state_kill_switch_decimal():
    """Verify hard kill-switch triggering on -3000 Decimal loss limit."""
    state = DailyRiskState(
        session_id="SESS_01",
        trading_date=date(2026, 11, 2),
        max_loss_limit=Decimal("3000.00"),
    )
    assert state.can_trade
    assert not state.is_kill_switch_active

    # Drawdown of -2500 -> Can still trade
    state.update_mtm(realized=Decimal("-1500.00"), unrealized=Decimal("-1000.00"))
    assert state.total_mtm == Decimal("-2500.00")
    assert state.can_trade

    # Drawdown reaches -3000 -> Kill-switch activates immediately
    state.update_mtm(realized=Decimal("-2000.00"), unrealized=Decimal("-1050.00"))
    assert state.total_mtm == Decimal("-3050.00")
    assert state.is_kill_switch_active
    assert not state.can_trade
    assert state.kill_switch_reason == RiskTriggerReason.DAILY_LOSS_LIMIT_BREACHED


def test_profit_protection_lock_and_trail_decimal():
    """Verify +3000 trigger, +500 floor lock, and dynamic step trailing with Decimal precision."""
    pp = ProfitProtectionState()

    # MTM at +2500 -> Not activated
    breach = pp.evaluate(
        current_mtm=Decimal("2500.00"),
        trigger_profit=Decimal("3000.00"),
        min_lock=Decimal("500.00"),
    )
    assert not pp.is_activated
    assert not breach

    # MTM touches +3000 -> Activated, floor locked at +500
    breach = pp.evaluate(
        current_mtm=Decimal("3000.00"),
        trigger_profit=Decimal("3000.00"),
        min_lock=Decimal("500.00"),
    )
    assert pp.is_activated
    assert pp.current_floor == Decimal("500.00")
    assert not breach

    # MTM advances to +4100 (+1100 above trigger -> 1 step of 1000)
    breach = pp.evaluate(
        current_mtm=Decimal("4100.00"),
        trigger_profit=Decimal("3000.00"),
        min_lock=Decimal("500.00"),
        step_profit=Decimal("1000.00"),
        step_lock=Decimal("500.00"),
    )
    assert pp.step_count == 1
    assert pp.current_floor == Decimal("1000.00")  # 500 + 1 * 500
    assert not breach

    # MTM drops back to +950 (below floor of 1000) -> Exit triggered!
    breach = pp.evaluate(
        current_mtm=Decimal("950.00"),
        trigger_profit=Decimal("3000.00"),
        min_lock=Decimal("500.00"),
        step_profit=Decimal("1000.00"),
        step_lock=Decimal("500.00"),
    )
    assert breach


def test_instrument_and_contract_metadata():
    """Verify contract and instrument metadata models."""
    inst = Instrument(
        symbol="NSE:NIFTY50-INDEX",
        exchange="NSE",
        instrument_type="INDEX",
        tick_size=Decimal("0.05"),
        lot_size=25,
    )
    assert inst.symbol == "NSE:NIFTY50-INDEX"

    contract = OptionContract(
        symbol="NSE:NIFTY26N0624500CE",
        underlying="NSE:NIFTY50-INDEX",
        strike=Decimal("24500.00"),
        option_type=OptionType.CE,
        expiry_date=date(2026, 11, 6),
        lot_size=25,
    )
    assert contract.strike == Decimal("24500.00")
    assert contract.option_type == OptionType.CE
