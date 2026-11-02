"""Unit tests for domain models."""

from datetime import date, datetime, time, timezone
import pytest
from pydantic import ValidationError

from src.common.constants import (
    OptionType,
    OrderSide,
    OrderStatus,
    OrderType,
    ProductType,
    RiskTriggerReason,
    SignalType,
)
from src.core.models import (
    BrokerOrder,
    Candle,
    DailyRiskState,
    Fill,
    MarketTick,
    OpeningRange,
    OptionContract,
    Position,
    ProfitProtectionState,
    StrategySignal,
)


def test_market_tick_validation():
    """Verify tick validation and UTC conversion."""
    dt = datetime(2026, 11, 2, 9, 30, 0, tzinfo=timezone.utc)
    tick = MarketTick(
        symbol="NSE:NIFTY50-INDEX",
        ltp=24500.50,
        volume=100,
        timestamp=dt,
    )
    assert tick.ltp == 24500.50
    assert tick.timestamp.tzinfo is not None

    with pytest.raises(ValidationError):
        # Negative price should fail
        MarketTick(symbol="NSE:NIFTY50-INDEX", ltp=-10.0, timestamp=dt)


def test_candle_high_low_validation():
    """Verify candle bar validation (high >= low)."""
    dt = datetime(2026, 11, 2, 9, 30, 0, tzinfo=timezone.utc)
    candle = Candle(
        symbol="NSE:NIFTY50-INDEX",
        timestamp=dt,
        open=24500.0,
        high=24550.0,
        low=24490.0,
        close=24530.0,
        volume=1500,
    )
    assert candle.high >= candle.low

    with pytest.raises(ValidationError):
        # Invalid bar where low > high
        Candle(
            symbol="NSE:NIFTY50-INDEX",
            timestamp=dt,
            open=24500.0,
            high=24400.0,
            low=24550.0,
            close=24450.0,
            volume=100,
        )


def test_opening_range_accumulation():
    """Verify opening range high/low accumulation and breakout detection."""
    dt = datetime(2026, 11, 2, 9, 16, 0, tzinfo=timezone.utc)
    orb = OpeningRange(session_date=date(2026, 11, 2))

    c1 = Candle(symbol="NSE:NIFTY50-INDEX", timestamp=dt, open=24500, high=24520, low=24480, close=24510, volume=100)
    c2 = Candle(symbol="NSE:NIFTY50-INDEX", timestamp=dt, open=24510, high=24560, low=24490, close=24540, volume=100)
    c3 = Candle(symbol="NSE:NIFTY50-INDEX", timestamp=dt, open=24540, high=24550, low=24460, close=24470, volume=100)

    orb.update_from_candle(c1)
    orb.update_from_candle(c2)
    orb.update_from_candle(c3)

    assert orb.high == 24560.0
    assert orb.low == 24460.0
    assert orb.candle_count == 3
    assert not orb.is_established

    orb.finalize()
    assert orb.is_established
    assert orb.is_broken_above(24560.05)
    assert not orb.is_broken_above(24560.00)
    assert orb.is_broken_below(24459.95)
    assert not orb.is_broken_below(24460.00)


def test_position_pnl_lifecycle():
    """Verify position net quantity, average prices, and realized/unrealized P&L."""
    dt = datetime(2026, 11, 2, 9, 45, 0, tzinfo=timezone.utc)
    pos = Position(
        session_id="SESS_01",
        symbol="NSE:NIFTY26N0624500CE",
        product_type=ProductType.INTRADAY,
    )

    # Buy 1 lot (25 qty) @ 100.0
    fill1 = Fill(
        client_order_id="ORD_01",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.BUY,
        fill_price=100.0,
        fill_qty=25,
        timestamp=dt,
    )
    pos.apply_fill(fill1)
    assert pos.is_open
    assert pos.net_qty == 25
    assert pos.avg_buy_price == 100.0
    assert pos.realized_pnl == 0.0

    # LTP moves to 120.0
    pos.update_ltp(120.0)
    assert pos.unrealized_pnl == (120.0 - 100.0) * 25  # +500.0
    assert pos.total_pnl == 500.0

    # Sell 1 lot (25 qty) @ 130.0 (Close position)
    fill2 = Fill(
        client_order_id="ORD_02",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.SELL,
        fill_price=130.0,
        fill_qty=25,
        timestamp=dt,
    )
    pos.apply_fill(fill2)
    assert not pos.is_open
    assert pos.net_qty == 0
    assert pos.realized_pnl == (130.0 - 100.0) * 25  # +750.0
    assert pos.unrealized_pnl == 0.0
    assert pos.total_pnl == 750.0


def test_daily_risk_state_kill_switch():
    """Verify hard kill-switch triggering on -3000 loss limit."""
    state = DailyRiskState(
        session_id="SESS_01",
        trading_date=date(2026, 11, 2),
        max_loss_limit=3000.0,
    )
    assert state.can_trade
    assert not state.is_kill_switch_active

    # Drawdown of -2500 -> Can still trade
    state.update_mtm(realized=-1500.0, unrealized=-1000.0)
    assert state.total_mtm == -2500.0
    assert state.can_trade

    # Drawdown reaches -3000 -> Kill-switch activates immediately
    state.update_mtm(realized=-2000.0, unrealized=-1050.0)
    assert state.total_mtm == -3050.0
    assert state.is_kill_switch_active
    assert not state.can_trade
    assert state.kill_switch_reason == RiskTriggerReason.DAILY_LOSS_LIMIT_BREACHED


def test_profit_protection_lock_and_trail():
    """Verify +3000 trigger, +500 floor lock, and dynamic step trailing."""
    pp = ProfitProtectionState()

    # MTM at +2500 -> Not activated
    breach = pp.evaluate(current_mtm=2500.0, trigger_profit=3000.0, min_lock=500.0)
    assert not pp.is_activated
    assert not breach

    # MTM touches +3000 -> Activated, floor locked at +500
    breach = pp.evaluate(current_mtm=3000.0, trigger_profit=3000.0, min_lock=500.0)
    assert pp.is_activated
    assert pp.current_floor == 500.0
    assert not breach

    # MTM advances to +4100 (+1100 above trigger -> 1 step of 1000)
    breach = pp.evaluate(
        current_mtm=4100.0,
        trigger_profit=3000.0,
        min_lock=500.0,
        step_profit=1000.0,
        step_lock=500.0,
    )
    assert pp.step_count == 1
    assert pp.current_floor == 1000.0  # 500 + 1 * 500
    assert not breach

    # MTM drops back to +950 (below floor of 1000) -> Exit triggered!
    breach = pp.evaluate(
        current_mtm=950.0,
        trigger_profit=3000.0,
        min_lock=500.0,
        step_profit=1000.0,
        step_lock=500.0,
    )
    assert breach
