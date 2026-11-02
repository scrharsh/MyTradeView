"""Pydantic v2 domain schemas and request/response contracts for the MyTradeView API."""

from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from src.common.constants import (
    BreakoutConfirmationMode,
    ExecutionMode,
    ExpiryDaySelectionMode,
    OptionType,
    OrderSide,
    OrderStatus,
    OrderType,
    ProductType,
    ProfitTrailingMode,
    RangeBoundaryMode,
    RiskTriggerReason,
    SessionPhase,
    StrategyState,
    StrategyTemplateType,
)
from src.common.time_utils import now_utc
from src.oms.models import OmsOrderStatus


# ------------------------------------------------------------------------------
# 1. Health & Telemetry Models
# ------------------------------------------------------------------------------

class SystemHealthResponse(BaseModel):
    status: str = "HEALTHY"
    execution_mode: str = "paper"
    timestamp: datetime = Field(default_factory=now_utc)
    broker_connected: bool = True
    broker_status: str = "CONNECTED"
    market_data_healthy: bool = True
    market_data_stale: bool = False
    websocket_state: str = "CONNECTED"
    database_connected: bool = True
    active_session_id: Optional[str] = None
    last_reconciliation_time: Optional[datetime] = None
    is_reconciled: bool = True


# ------------------------------------------------------------------------------
# 2. Strategy Template & Configuration Models
# ------------------------------------------------------------------------------

class StrategyParameterDef(BaseModel):
    name: str
    param_type: str
    default_value: Any
    description: str
    is_required: bool = True
    allowed_values: Optional[List[Any]] = None


class StrategyTemplateResponse(BaseModel):
    template_type: str
    display_name: str
    description: str
    supported_underlyings: List[str]
    supported_order_types: List[int]
    parameters: Dict[str, StrategyParameterDef]


class StrategyValidateRequest(BaseModel):
    template_type: str = "NIFTY_ORB"
    parameters: Dict[str, Any]


class StrategyValidateResponse(BaseModel):
    is_valid: bool
    validated_parameters: Dict[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)
    unresolved_decisions: List[str] = Field(default_factory=list)


class StrategyConfigVersionResponse(BaseModel):
    config_id: str
    version: int
    underlying_symbol: str
    parameters: Dict[str, Any]
    is_active: bool
    created_at: datetime


class StrategyConfigCreateRequest(BaseModel):
    config_id: Optional[str] = None
    template_type: str = "NIFTY_ORB"
    underlying_symbol: str = "NSE:NIFTY50-INDEX"
    parameters: Dict[str, Any]


class StrategyConfigVersionRequest(BaseModel):
    parameters: Dict[str, Any]
    set_active: bool = True


# ------------------------------------------------------------------------------
# 3. Market Data & Candlestick Models
# ------------------------------------------------------------------------------

class CandleResponseItem(BaseModel):
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = 0


class CandlesResponse(BaseModel):
    symbol: str
    resolution: str = "1m"
    provenance: str = "DATABASE"
    is_synthetic: bool = False
    candle_count: int
    candles: List[CandleResponseItem]


class InstrumentResponseItem(BaseModel):
    symbol: str
    exchange: str = "NSE"
    instrument_type: str
    underlying: str
    strike: Optional[Decimal] = None
    option_type: Optional[str] = None
    expiry_date: Optional[date] = None
    lot_size: int = 25
    tick_size: Decimal = Decimal("0.05")
    is_active: bool = True


# ------------------------------------------------------------------------------
# 4. Trading Session Models
# ------------------------------------------------------------------------------

class OpeningRangeResponse(BaseModel):
    is_established: bool = False
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    range_width: Optional[Decimal] = None
    start_time: str = "09:16:00"
    end_time: str = "09:45:00"
    candle_count: int = 0


class SessionActiveResponse(BaseModel):
    session_id: str
    trading_date: date
    execution_mode: str = "paper"
    phase: str
    is_active: bool
    started_at: datetime
    ended_at: Optional[datetime] = None
    strategy_state: str = "WAITING_FOR_ORB"
    opening_range: OpeningRangeResponse
    active_contract_symbol: Optional[str] = None


class SessionActionRequest(BaseModel):
    action: str  # start, stop, pause, resume


# ------------------------------------------------------------------------------
# 5. Risk Monitoring & Kill Switch Models
# ------------------------------------------------------------------------------

class RiskSnapshotResponse(BaseModel):
    session_id: str
    timestamp: datetime = Field(default_factory=now_utc)
    realized_pnl: Decimal = Decimal("0.00")
    unrealized_pnl: Decimal = Decimal("0.00")
    total_mtm: Decimal = Decimal("0.00")
    peak_mtm: Decimal = Decimal("0.00")
    current_drawdown: Decimal = Decimal("0.00")
    max_daily_loss_limit: Decimal = Decimal("3000.00")
    is_circuit_breaker_active: bool = False
    circuit_breaker_reason: Optional[str] = None
    is_kill_switch_active: bool = False
    kill_switch_reason: Optional[str] = None
    is_profit_protection_active: bool = False
    profit_protection_status: str = "INACTIVE"
    profit_floor: Decimal = Decimal("0.00")
    open_positions_count: int = 0
    is_data_stale: bool = False


class KillSwitchRequest(BaseModel):
    reason: str = "MANUAL_OVERRIDE"
    operator: str = "Operator"
    notes: str = "Emergency liquidation requested via frontend workstation"


class KillSwitchResponse(BaseModel):
    success: bool
    status: str
    reason: str
    orders_cancelled_count: int
    positions_closed_count: int
    timestamp: datetime = Field(default_factory=now_utc)


# ------------------------------------------------------------------------------
# 6. Orders & Positions Models
# ------------------------------------------------------------------------------

class OrderTransitionResponseItem(BaseModel):
    from_status: str
    to_status: str
    filled_qty: int
    avg_price: Decimal
    message: str
    timestamp: datetime


class FillResponseItem(BaseModel):
    fill_id: str
    client_order_id: str
    broker_order_id: Optional[str] = None
    symbol: str
    side: str
    fill_price: Decimal
    fill_qty: int
    timestamp: datetime


class OrderResponseItem(BaseModel):
    client_order_id: str
    broker_order_id: Optional[str] = None
    session_id: str
    symbol: str
    side: str
    order_type: str
    product_type: str = "INTRADAY"
    qty: int
    filled_qty: int
    remaining_qty: int
    oms_status: str
    status: str
    avg_fill_price: Decimal
    limit_price: Decimal
    tag: str
    status_message: str
    submitted_at: datetime
    updated_at: datetime
    transitions: List[OrderTransitionResponseItem] = Field(default_factory=list)
    fills: List[FillResponseItem] = Field(default_factory=list)


class PositionResponseItem(BaseModel):
    position_id: str
    session_id: str
    symbol: str
    product_type: str = "INTRADAY"
    net_qty: int
    buy_qty: int
    sell_qty: int
    avg_buy_price: Decimal
    avg_sell_price: Decimal
    current_ltp: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_pnl: Decimal
    is_open: bool
    opened_at: datetime
    closed_at: Optional[datetime] = None


class SquareOffRequest(BaseModel):
    reason: str = "MANUAL_SQUARE_OFF"


# ------------------------------------------------------------------------------
# 7. Backtest Lab Models
# ------------------------------------------------------------------------------

class BacktestRunRequest(BaseModel):
    config_id: Optional[str] = None
    template_type: str = "NIFTY_ORB"
    parameters: Optional[Dict[str, Any]] = None
    start_date: date
    end_date: date
    dataset_id: str = "DEFAULT_SYNTHETIC"
    slippage_points: Decimal = Decimal("0.50")
    brokerage_per_order: Decimal = Decimal("20.00")
    initial_capital: Decimal = Decimal("100000.00")


class BacktestEquityPoint(BaseModel):
    timestamp: datetime
    equity: Decimal
    drawdown: Decimal


class BacktestTradeItem(BaseModel):
    trade_id: str
    symbol: str
    side: str
    entry_time: datetime
    entry_price: Decimal
    exit_time: datetime
    exit_price: Decimal
    qty: int
    gross_pnl: Decimal
    transaction_costs: Decimal
    net_pnl: Decimal
    exit_reason: str
    duration_minutes: int


class BacktestSummaryResponse(BaseModel):
    run_id: str
    dataset_id: str
    start_date: date
    end_date: date
    strategy_name: str
    strategy_version: int
    is_synthetic: bool = False
    net_profit: Decimal
    total_trades: int
    win_rate_pct: Decimal
    profit_factor: Decimal
    max_drawdown_amount: Decimal
    max_drawdown_pct: Decimal
    sharpe_ratio: Decimal
    expectancy: Decimal
    created_at: datetime


class BacktestDetailsResponse(BaseModel):
    summary: BacktestSummaryResponse
    metrics: Dict[str, Any]
    diagnostics: Dict[str, Any]
    equity_curve: List[BacktestEquityPoint]
    trades: List[BacktestTradeItem]


class BenchmarkRequest(BaseModel):
    run_id: str
    stockmock_csv_content: str


class BenchmarkResponse(BaseModel):
    matched_trades_count: int
    discrepancy_count: int
    pnl_diff: Decimal
    is_matched: bool
    summary: str

