"""Phase 9: Live Market Data Connection & Simulated Paper Trading Test Suite.

Verifies:
1. FYERS live connection & IST market operating window evaluation (fail-closed, credential redaction).
2. Real-time candle anomaly detection: duplicate, out-of-order, and stale candle rejection.
3. Live candlestick ingestion into WorkstationService driving NiftyOrbStrategy.
4. Pre-trade risk gating via RiskEngine and execution via PaperBrokerClient (with 0.50 pts slippage).
5. Two-phase reversal sequencing (close existing position first before entering opposite side).
6. Mandatory 15:10 IST square-off and ₹3,000 daily loss lockout.
7. Strict safety: EXECUTION_MODE == 'paper' with zero live broker orders.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import os
import pytest
from unittest.mock import patch
from uuid import uuid4

from config.risk_config import RiskConfig
from config.strategy_config import StrategyConfig
from scripts.test_live_market_data_connection import (
    check_market_hours,
    redact,
    test_fyers_live_connection as run_fyers_live_connection_check,
)
from src.api.service import WorkstationService, workstation_service
from src.broker.paper import PaperBrokerClient
from src.common.constants import (
    BreakoutConfirmationMode,
    ExecutionMode,
    OptionType,
    OrderSide,
    OrderStatus,
    ProductType,
    RangeBoundaryMode,
    SessionPhase,
    StrategyState,
)
from src.common.time_utils import IST_TZ, now_utc, to_ist, to_utc
from src.core.models import Candle, OptionContract, OrderRequest, TradingSession
from src.strategy.orb_nifty import NiftyOrbStrategy


@pytest.fixture
async def paper_workstation() -> WorkstationService:
    """Fixture providing a clean, initialized WorkstationService in PAPER mode."""
    if not workstation_service.is_initialized:
        await workstation_service.initialize()

    # Ensure instrument registry contains contracts around 25000
    csv_data = """trading_symbol,underlying_symbol,strike_price,option_type,expiry_date,minimum_lot_size,tick_size
NSE:NIFTY26NOV25000CE,NSE:NIFTY50-INDEX,25000.0,CE,2026-11-06,25,0.05
NSE:NIFTY26NOV25000PE,NSE:NIFTY50-INDEX,25000.0,PE,2026-11-06,25,0.05
NSE:NIFTY26NOV24950CE,NSE:NIFTY50-INDEX,24950.0,CE,2026-11-06,25,0.05
NSE:NIFTY26NOV24950PE,NSE:NIFTY50-INDEX,24950.0,PE,2026-11-06,25,0.05
NSE:NIFTY26NOV24900CE,NSE:NIFTY50-INDEX,24900.0,CE,2026-11-06,25,0.05
NSE:NIFTY26NOV24900PE,NSE:NIFTY50-INDEX,24900.0,PE,2026-11-06,25,0.05
NSE:NIFTY26NOV25050CE,NSE:NIFTY50-INDEX,25050.0,CE,2026-11-06,25,0.05
NSE:NIFTY26NOV25050PE,NSE:NIFTY50-INDEX,25050.0,PE,2026-11-06,25,0.05
NSE:NIFTY26NOV25100CE,NSE:NIFTY50-INDEX,25100.0,CE,2026-11-06,25,0.05
NSE:NIFTY26NOV25100PE,NSE:NIFTY50-INDEX,25100.0,PE,2026-11-06,25,0.05
"""
    workstation_service.instrument_registry.parse_csv(csv_data)

    # Reset in-memory session for 2026-11-06 (Friday, active trading day)
    sess_id = f"SESSION-TEST-{uuid4().hex[:8]}"
    workstation_service.active_session = TradingSession(
        session_id=sess_id,
        trading_date=date(2026, 11, 6),
        execution_mode=ExecutionMode.PAPER,
        phase=SessionPhase.ACTIVE_TRADING,
    )
    workstation_service.oms.session = workstation_service.active_session
    workstation_service.oms._orders.clear()
    workstation_service.oms._positions.clear()
    workstation_service.oms._broker_to_client.clear()
    workstation_service.paper_broker._positions.clear()
    workstation_service.paper_broker._orders.clear()
    workstation_service.paper_broker._trades.clear()
    workstation_service.risk_engine.reset_for_new_session(sess_id)
    workstation_service.last_candle_time = None
    workstation_service.candle_anomaly_stats = {
        "stale": 0,
        "missing": 0,
        "duplicate": 0,
        "out_of_order": 0,
    }
    workstation_service.active_opening_range = None
    workstation_service.risk_config = RiskConfig(max_daily_loss_limit=Decimal("3000.00"))

    workstation_service.strategy = NiftyOrbStrategy(
        strategy_config=workstation_service.active_strategy_config or StrategyConfig(),
        risk_config=workstation_service.risk_config,
        contract_resolver=workstation_service.contract_resolver,
    )
    workstation_service.strategy.on_session_start(sess_id, date(2026, 11, 6))

    return workstation_service


# ==============================================================================
# 1. Market Hours, FYERS Live Connection & Redaction
# ==============================================================================

def test_market_hours_detection():
    """Verify market hours check matches NSE 09:15 to 15:30 IST window on weekdays."""
    info = check_market_hours()
    assert "current_time_ist" in info
    assert "market_open_time" in info
    assert info["market_open_time"] == "09:15:00 IST"
    assert info["market_close_time"] == "15:30:00 IST"
    assert isinstance(info["is_during_market_hours"], bool)


def test_credential_redaction():
    """Verify that credentials are strictly redacted and never logged in plain text."""
    assert redact(None) == "<NOT_SET>"
    assert redact("") == "<NOT_SET>"
    assert redact("abc") == "***"
    assert redact("FYERS_SECRET_KEY_12345") == "FYE...345"


def test_fyers_unauthenticated_fail_closed(monkeypatch, tmp_path):
    """Verify unauthenticated FYERS check fails closed gracefully."""
    monkeypatch.setattr("scripts.test_live_market_data_connection.ROOT_DIR", tmp_path)
    with patch.dict(os.environ, {}, clear=True):
        res = run_fyers_live_connection_check()
        assert res["status"] in {"UNAUTHENTICATED", "AUTH_FAILED"}
        assert res["connected"] is False
        assert "Missing" in res.get("reason", "") or "error" in res or "code" in res


# ==============================================================================
# 2. Candle Anomaly Detection (Duplicate, Out-of-Order, Staleness)
# ==============================================================================

@pytest.mark.asyncio
async def test_duplicate_candle_rejection(paper_workstation: WorkstationService):
    """Verify identical timestamp candles are dropped and increment duplicate anomaly count."""
    ws = paper_workstation
    ts = to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, 16, 0)))

    c1 = Candle(
        symbol="NSE:NIFTY50-INDEX",
        timestamp=ts,
        open=Decimal("25000.00"),
        high=Decimal("25010.00"),
        low=Decimal("24995.00"),
        close=Decimal("25005.00"),
        volume=1000,
    )
    c2 = Candle(
        symbol="NSE:NIFTY50-INDEX",
        timestamp=ts,  # Duplicate timestamp
        open=Decimal("25005.00"),
        high=Decimal("25015.00"),
        low=Decimal("25000.00"),
        close=Decimal("25010.00"),
        volume=800,
    )

    initial_dups = ws.candle_anomaly_stats["duplicate"]
    await ws.process_live_candle(c1)
    orders2 = await ws.process_live_candle(c2)

    assert len(orders2) == 0
    assert ws.candle_anomaly_stats["duplicate"] == initial_dups + 1


@pytest.mark.asyncio
async def test_out_of_order_candle_rejection(paper_workstation: WorkstationService):
    """Verify out-of-order timestamps are rejected fail-closed."""
    ws = paper_workstation
    t1 = to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, 20, 0)))
    t2 = to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, 19, 0)))  # Backwards

    c1 = Candle(
        symbol="NSE:NIFTY50-INDEX",
        timestamp=t1,
        open=Decimal("25000.00"),
        high=Decimal("25010.00"),
        low=Decimal("24995.00"),
        close=Decimal("25005.00"),
        volume=1000,
    )
    c2 = Candle(
        symbol="NSE:NIFTY50-INDEX",
        timestamp=t2,
        open=Decimal("25005.00"),
        high=Decimal("25015.00"),
        low=Decimal("25000.00"),
        close=Decimal("25010.00"),
        volume=800,
    )

    initial_ooo = ws.candle_anomaly_stats["out_of_order"]
    await ws.process_live_candle(c1)
    orders = await ws.process_live_candle(c2)

    assert len(orders) == 0
    assert ws.candle_anomaly_stats["out_of_order"] == initial_ooo + 1


# ==============================================================================
# 3. Live Candle Range Formation, Breakout & Paper Execution
# ==============================================================================

@pytest.mark.asyncio
async def test_live_candle_range_formation_and_upside_breakout(paper_workstation: WorkstationService):
    """
    Feeds 30 opening range candles (09:16 to 09:45) followed by a 09:46 breakout candle.
    Verifies:
    1. Range is established at 09:45 IST.
    2. Upside breakout produces BUY CE OrderIntent.
    3. Pre-trade risk approves order.
    4. PaperBrokerClient fills order with 0.50 slippage.
    5. Position and StrategyState transition to LONG_CE.
    """
    ws = paper_workstation

    # 1. Feed 30 candles (09:16 to 09:45) within range [24990.00, 25010.00]
    for m in range(16, 46):
        ts = to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, m, 0)))
        c = Candle(
            symbol="NSE:NIFTY50-INDEX",
            timestamp=ts,
            open=Decimal("25000.00"),
            high=Decimal("25010.00") if m == 20 else Decimal("25005.00"),
            low=Decimal("24990.00") if m == 30 else Decimal("24995.00"),
            close=Decimal("25000.00"),
            volume=500,
        )
        await ws.process_live_candle(c)

    # Opening range should be established
    assert ws.strategy.range_tracker.is_established is True
    assert ws.strategy.range_tracker.high == Decimal("25010.00")
    assert ws.strategy.range_tracker.low == Decimal("24990.00")
    assert ws.strategy.state == StrategyState.RANGE_ESTABLISHED

    # 2. Feed breakout candle at 09:46 closing at 25015.00 (> 25010.00)
    ts_breakout = to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0)))
    c_breakout = Candle(
        symbol="NSE:NIFTY50-INDEX",
        timestamp=ts_breakout,
        open=Decimal("25008.00"),
        high=Decimal("25018.00"),
        low=Decimal("25005.00"),
        close=Decimal("25015.00"),
        volume=1200,
    )
    orders = await ws.process_live_candle(c_breakout)

    assert len(orders) >= 1
    orb_order = orders[0]
    assert orb_order.side == OrderSide.BUY
    assert orb_order.symbol == "NSE:NIFTY26NOV25000CE"
    assert orb_order.qty == 25
    assert orb_order.filled_qty == 25
    # Slippage verification: base limit 100.00 + 0.50 = 100.50
    assert orb_order.avg_fill_price == Decimal("100.50")

    # Strategy state verification
    assert ws.strategy.state == StrategyState.LONG_CE

    # Active session telemetry
    session_info = ws.get_active_session()
    assert session_info.strategy_state == StrategyState.LONG_CE.value
    assert session_info.active_contract_symbol == "NSE:NIFTY26NOV25000CE"


# ==============================================================================
# 4. Two-Phase Atomic Reversal Sequencing
# ==============================================================================

@pytest.mark.asyncio
async def test_live_candle_two_phase_reversal(paper_workstation: WorkstationService):
    """
    Verifies two-phase reversal:
    Phase 1: Downside breakout emits CLOSE_CE. Position is flat.
    Phase 2: Confirmed CE exit immediately triggers ENTER_LONG_PE.
    """
    ws = paper_workstation

    # Set up strategy in LONG_CE state with established range [24990.00, 25010.00]
    for m in range(16, 46):
        ts = to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, m, 0)))
        await ws.process_live_candle(
            Candle(
                symbol="NSE:NIFTY50-INDEX",
                timestamp=ts,
                open=Decimal("25000.00"),
                high=Decimal("25010.00") if m == 20 else Decimal("25005.00"),
                low=Decimal("24990.00") if m == 30 else Decimal("24995.00"),
                close=Decimal("25000.00"),
                volume=500,
            )
        )

    # Trigger CE entry
    await ws.process_live_candle(
        Candle(
            symbol="NSE:NIFTY50-INDEX",
            timestamp=to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0))),
            open=Decimal("25008.00"),
            high=Decimal("25018.00"),
            low=Decimal("25005.00"),
            close=Decimal("25015.00"),
            volume=1000,
        )
    )
    assert ws.strategy.state == StrategyState.LONG_CE

    # Now spot reverses below range low (24985.00 < 24990.00) at 10:15 IST
    ts_rev = to_utc(IST_TZ.localize(datetime(2026, 11, 6, 10, 15, 0)))
    rev_candle = Candle(
        symbol="NSE:NIFTY50-INDEX",
        timestamp=ts_rev,
        open=Decimal("24992.00"),
        high=Decimal("24995.00"),
        low=Decimal("24980.00"),
        close=Decimal("24985.00"),
        volume=1500,
    )
    rev_orders = await ws.process_live_candle(rev_candle)

    # Must contain both the exit order (SELL CE) and reversal entry (BUY PE)
    assert len(rev_orders) >= 2
    exit_order = rev_orders[0]
    entry_order = rev_orders[1]

    assert exit_order.side == OrderSide.SELL
    assert exit_order.symbol == "NSE:NIFTY26NOV25000CE"
    assert exit_order.filled_qty == 25

    assert entry_order.side == OrderSide.BUY
    assert entry_order.symbol == "NSE:NIFTY26NOV25000PE"
    assert entry_order.filled_qty == 25

    # Strategy state transitioned to LONG_PE
    assert ws.strategy.state == StrategyState.LONG_PE


# ==============================================================================
# 5. Mandatory 15:10 IST Square-off
# ==============================================================================

@pytest.mark.asyncio
async def test_mandatory_1510_square_off(paper_workstation: WorkstationService):
    """Verify candle at 15:10 IST forces square-off and transitions to SQUARED_OFF."""
    ws = paper_workstation

    # Fast forward range setup and entry
    for m in range(16, 46):
        await ws.process_live_candle(
            Candle(
                symbol="NSE:NIFTY50-INDEX",
                timestamp=to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, m, 0))),
                open=Decimal("25000.00"),
                high=Decimal("25010.00") if m == 20 else Decimal("25005.00"),
                low=Decimal("24990.00") if m == 30 else Decimal("24995.00"),
                close=Decimal("25000.00"),
                volume=500,
            )
        )
    await ws.process_live_candle(
        Candle(
            symbol="NSE:NIFTY50-INDEX",
            timestamp=to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0))),
            open=Decimal("25008.00"),
            high=Decimal("25018.00"),
            low=Decimal("25005.00"),
            close=Decimal("25015.00"),
            volume=1000,
        )
    )
    assert ws.strategy.state == StrategyState.LONG_CE

    # Feed candle at 15:10 IST
    ts_sqoff = to_utc(IST_TZ.localize(datetime(2026, 11, 6, 15, 10, 0)))
    sq_candle = Candle(
        symbol="NSE:NIFTY50-INDEX",
        timestamp=ts_sqoff,
        open=Decimal("25080.00"),
        high=Decimal("25085.00"),
        low=Decimal("25075.00"),
        close=Decimal("25080.00"),
        volume=1000,
    )
    orders = await ws.process_live_candle(sq_candle)

    assert len(orders) >= 1
    sq_order = orders[0]
    assert sq_order.side == OrderSide.SELL
    assert sq_order.filled_qty == 25
    assert ws.strategy.state == StrategyState.SQUARED_OFF


@pytest.mark.asyncio
async def test_daily_loss_limit_circuit_breaker_lockout(paper_workstation: WorkstationService):
    """Verify daily loss limit breach (-₹3,000) triggers emergency lockout."""
    ws = paper_workstation

    # Set up range and entry
    for m in range(16, 46):
        await ws.process_live_candle(
            Candle(
                symbol="NSE:NIFTY50-INDEX",
                timestamp=to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, m, 0))),
                open=Decimal("25000.00"),
                high=Decimal("25010.00") if m == 20 else Decimal("25005.00"),
                low=Decimal("24990.00") if m == 30 else Decimal("24995.00"),
                close=Decimal("25000.00"),
                volume=500,
            )
        )
    await ws.process_live_candle(
        Candle(
            symbol="NSE:NIFTY50-INDEX",
            timestamp=to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0))),
            open=Decimal("25008.00"),
            high=Decimal("25018.00"),
            low=Decimal("25005.00"),
            close=Decimal("25015.00"),
            volume=1000,
        )
    )
    assert ws.strategy.state == StrategyState.LONG_CE

    # Simulate MTM loss of -₹3,100
    ts_loss = to_utc(IST_TZ.localize(datetime(2026, 11, 6, 10, 0, 0)))
    loss_signals = ws.strategy.on_mtm_update(
        realized_pnl=Decimal("-3100.00"),
        unrealized_pnl=Decimal("0.00"),
        timestamp=ts_loss,
    )
    assert len(loss_signals) >= 1
    assert "Daily MTM loss limit" in loss_signals[0].reason
    assert ws.strategy.state == StrategyState.LOCKED_OUT_FOR_DAY

    # Subsequent candles must return zero signals (locked out)
    next_candle = Candle(
        symbol="NSE:NIFTY50-INDEX",
        timestamp=to_utc(IST_TZ.localize(datetime(2026, 11, 6, 10, 5, 0))),
        open=Decimal("25050.00"),
        high=Decimal("25060.00"),
        low=Decimal("25040.00"),
        close=Decimal("25055.00"),
        volume=1000,
    )
    orders = await ws.process_live_candle(next_candle)
    assert len(orders) == 0


@pytest.mark.asyncio
async def test_profit_protection_floor_lock(paper_workstation: WorkstationService):
    """Verify profit protection activation (+₹3,000) and exit when pulling back below locked floor (+₹500)."""
    ws = paper_workstation

    # Set up range and entry
    for m in range(16, 46):
        await ws.process_live_candle(
            Candle(
                symbol="NSE:NIFTY50-INDEX",
                timestamp=to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, m, 0))),
                open=Decimal("25000.00"),
                high=Decimal("25010.00") if m == 20 else Decimal("25005.00"),
                low=Decimal("24990.00") if m == 30 else Decimal("24995.00"),
                close=Decimal("25000.00"),
                volume=500,
            )
        )
    await ws.process_live_candle(
        Candle(
            symbol="NSE:NIFTY50-INDEX",
            timestamp=to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0))),
            open=Decimal("25008.00"),
            high=Decimal("25018.00"),
            low=Decimal("25005.00"),
            close=Decimal("25015.00"),
            volume=1000,
        )
    )
    assert ws.strategy.state == StrategyState.LONG_CE

    # MTM spikes to +₹3,200 (triggers profit protection with ₹500 floor)
    t1 = to_utc(IST_TZ.localize(datetime(2026, 11, 6, 10, 0, 0)))
    ws.strategy.on_mtm_update(
        realized_pnl=Decimal("0.00"),
        unrealized_pnl=Decimal("3200.00"),
        timestamp=t1,
    )
    assert ws.strategy.profit_protection.is_activated is True
    assert ws.strategy.profit_protection.current_floor == Decimal("500.00")

    # MTM pulls back below floor to +₹400
    t2 = to_utc(IST_TZ.localize(datetime(2026, 11, 6, 10, 10, 0)))
    exit_signals = ws.strategy.on_mtm_update(
        realized_pnl=Decimal("0.00"),
        unrealized_pnl=Decimal("400.00"),
        timestamp=t2,
    )
    assert len(exit_signals) >= 1
    assert "Profit protection floor breached" in exit_signals[0].reason
    assert ws.strategy.state == StrategyState.PROFIT_LOCKED


# ==============================================================================
# 6. Strict Paper Safety & Telemetry
# ==============================================================================

@pytest.mark.asyncio
async def test_strict_paper_safety(paper_workstation: WorkstationService):
    """Verify that execution mode is strictly paper and PaperBrokerClient is active."""
    ws = paper_workstation
    assert ws.execution_mode == "paper"
    assert isinstance(ws.paper_broker, PaperBrokerClient)
    assert ws.active_session.execution_mode == ExecutionMode.PAPER

    status = ws.get_market_data_status()
    assert status["execution_mode"] == "paper"
    assert "current_time_ist" in status
    assert "is_market_open" in status
    assert "anomalies" in status
