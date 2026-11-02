"""Comprehensive domain models for the automated trading platform using Decimal precision."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.common.constants import (
    AuditEventType,
    ExecutionMode,
    OptionType,
    OrderSide,
    OrderStatus,
    OrderType,
    ProductType,
    RiskTriggerReason,
    SessionPhase,
    SignalType,
    StrategyState,
    can_transition_order,
    can_transition_strategy,
)
from src.common.exceptions import InvalidStateTransitionError
from src.common.time_utils import now_utc, to_utc


# ------------------------------------------------------------------------------
# 1. Trading Session & Strategy Configuration Version
# ------------------------------------------------------------------------------

class TradingSession(BaseModel):
    """Trading session model tracking lifecycle and operational mode (Default: PAPER)."""
    model_config = ConfigDict(frozen=True)

    session_id: str = Field(description="Unique session identifier, e.g. SESSION_20261102")
    trading_date: date = Field(description="Calendar date of trading session")
    execution_mode: ExecutionMode = Field(default=ExecutionMode.PAPER)
    phase: SessionPhase = Field(default=SessionPhase.PRE_MARKET)
    is_active: bool = Field(default=True)
    started_at: datetime = Field(default_factory=now_utc)
    ended_at: Optional[datetime] = Field(default=None)


class StrategyConfigSnapshot(BaseModel):
    """Versioned snapshot of strategy configuration for auditing."""
    model_config = ConfigDict(frozen=True)

    config_id: str = Field(default_factory=lambda: f"CFG_{uuid4().hex[:12]}")
    version: int = Field(default=1, ge=1)
    underlying_symbol: str = "NSE:NIFTY50-INDEX"
    orb_start_time: time = time(9, 16, 0)
    orb_end_time: time = time(9, 45, 0)
    mandatory_exit_time: time = time(15, 10, 0)
    lots: int = 1
    lot_size: int = 25
    max_daily_loss_limit: Decimal = Decimal("3000.00")
    profit_protection_trigger: Decimal = Decimal("3000.00")
    profit_protection_min_lock: Decimal = Decimal("500.00")
    created_at: datetime = Field(default_factory=now_utc)


# ------------------------------------------------------------------------------
# 2. Market Data: Ticks, Candles & Opening Range
# ------------------------------------------------------------------------------

class MarketTick(BaseModel):
    """Normalized real-time market tick using Decimal price."""
    model_config = ConfigDict(frozen=True)

    symbol: str = Field(description="Instrument symbol, e.g. NSE:NIFTY50-INDEX")
    ltp: Decimal = Field(gt=0, description="Last traded price")
    volume: int = Field(default=0, ge=0)
    timestamp: datetime = Field(description="Timestamp of tick arrival in UTC")
    bid_price: Optional[Decimal] = Field(default=None, ge=0)
    ask_price: Optional[Decimal] = Field(default=None, ge=0)

    @field_validator("timestamp")
    @classmethod
    def ensure_timezone_aware(cls, v: datetime) -> datetime:
        return to_utc(v)


class Candle(BaseModel):
    """OHLCV candlestick bar using Decimal prices."""
    model_config = ConfigDict(frozen=True)

    symbol: str
    timestamp: datetime = Field(description="Bar start timestamp in UTC")
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: int = Field(default=0, ge=0)

    @field_validator("timestamp")
    @classmethod
    def ensure_timezone_aware(cls, v: datetime) -> datetime:
        return to_utc(v)

    @model_validator(mode="after")
    def validate_candle_bounds(self) -> "Candle":
        if self.high < self.low:
            raise ValueError(f"High ({self.high}) cannot be less than low ({self.low})")
        if self.high < self.open or self.high < self.close:
            raise ValueError(f"High ({self.high}) must be >= open ({self.open}) and close ({self.close})")
        if self.low > self.open or self.low > self.close:
            raise ValueError(f"Low ({self.low}) must be <= open ({self.open}) and close ({self.close})")
        return self


class OpeningRange(BaseModel):
    """State of the strategy opening range using Decimal high and low."""
    symbol: str = "NSE:NIFTY50-INDEX"
    session_date: date
    start_time: time = time(9, 16, 0)
    end_time: time = time(9, 45, 0)
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    is_established: bool = False
    candle_count: int = 0

    def update_from_candle(self, candle: Candle) -> None:
        """Update range boundaries from incoming candle."""
        if self.high is None or candle.high > self.high:
            self.high = candle.high
        if self.low is None or candle.low < self.low:
            self.low = candle.low
        self.candle_count += 1

    def finalize(self) -> None:
        """Finalize and freeze the opening range."""
        if self.high is not None and self.low is not None:
            self.is_established = True

    def is_broken_above(self, price: Decimal) -> bool:
        """Check if price breaks strictly above range high."""
        return self.is_established and self.high is not None and price > self.high

    def is_broken_below(self, price: Decimal) -> bool:
        """Check if price breaks strictly below range low."""
        return self.is_established and self.low is not None and price < self.low


# ------------------------------------------------------------------------------
# 3. Instrument & Option Contract Metadata
# ------------------------------------------------------------------------------

class Instrument(BaseModel):
    """General instrument metadata."""
    model_config = ConfigDict(frozen=True)

    symbol: str = Field(description="Trading symbol, e.g. NSE:NIFTY50-INDEX")
    exchange: str = "NSE"
    instrument_type: str = "INDEX"  # INDEX, OPTIDX, FUTIDX
    tick_size: Decimal = Decimal("0.05")
    lot_size: int = Field(default=25, ge=1)
    is_active: bool = True


class OptionContract(BaseModel):
    """Option contract model representing resolved derivative."""
    model_config = ConfigDict(frozen=True)

    symbol: str = Field(description="FYERS trading symbol, e.g. NSE:NIFTY26N0624500CE")
    underlying: str = Field(default="NSE:NIFTY50-INDEX")
    strike: Decimal = Field(gt=0)
    option_type: OptionType
    expiry_date: date
    lot_size: int = Field(default=25, ge=1)
    tick_size: Decimal = Decimal("0.05")


# ------------------------------------------------------------------------------
# 4. Strategy Signal
# ------------------------------------------------------------------------------

class StrategySignal(BaseModel):
    """Actionable signal generated by the strategy engine."""
    model_config = ConfigDict(frozen=True)

    signal_id: str = Field(default_factory=lambda: f"SIG_{uuid4().hex[:12]}")
    session_id: str
    timestamp: datetime = Field(default_factory=now_utc)
    signal_type: SignalType
    underlying_price: Decimal = Field(gt=0)
    target_contract: Optional[OptionContract] = None
    reason: str
    sequence_number: int = Field(ge=1)


# ------------------------------------------------------------------------------
# 5. Orders, Fills & Positions
# ------------------------------------------------------------------------------

class OrderRequest(BaseModel):
    """Outbound order request generated by the OMS."""
    model_config = ConfigDict(frozen=True)

    client_order_id: str = Field(description="Unique client order token")
    session_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    qty: int = Field(gt=0)
    product_type: ProductType = ProductType.INTRADAY
    limit_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    stop_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    tag: str = Field(max_length=20, description="FYERS order tag for client reconciliation")
    created_at: datetime = Field(default_factory=now_utc)


class Fill(BaseModel):
    """Execution fill report representing full or partial order execution."""
    model_config = ConfigDict(frozen=True)

    fill_id: str = Field(default_factory=lambda: f"FILL_{uuid4().hex[:12]}")
    client_order_id: str
    broker_order_id: Optional[str] = None
    symbol: str
    side: OrderSide
    fill_price: Decimal = Field(gt=0)
    fill_qty: int = Field(gt=0)
    timestamp: datetime = Field(default_factory=now_utc)


class BrokerOrder(BaseModel):
    """Stateful order tracking model managed by the OMS."""
    client_order_id: str
    broker_order_id: Optional[str] = None
    session_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    product_type: ProductType
    qty: int = Field(gt=0)
    filled_qty: int = Field(default=0, ge=0)
    remaining_qty: int = Field(ge=0)
    status: OrderStatus = OrderStatus.NEW
    avg_fill_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    limit_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    tag: str
    status_message: Optional[str] = None
    submitted_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    @field_validator("remaining_qty", mode="before")
    @classmethod
    def set_initial_remaining_qty(cls, v: Any, info) -> int:
        if v is None:
            return info.data.get("qty", 0)
        return v

    def apply_transition(
        self,
        new_status: OrderStatus,
        filled_qty: Optional[int] = None,
        avg_price: Optional[Decimal] = None,
        message: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Apply state transition with strict validity check."""
        if not can_transition_order(self.status, new_status):
            raise InvalidStateTransitionError(
                entity=f"Order[{self.client_order_id}]",
                current_state=self.status.name,
                attempted_state=new_status.name,
            )

        self.status = new_status
        if filled_qty is not None:
            self.filled_qty = filled_qty
            self.remaining_qty = max(0, self.qty - filled_qty)
        if avg_price is not None:
            self.avg_fill_price = avg_price
        if message is not None:
            self.status_message = message
        self.updated_at = timestamp or now_utc()


class Position(BaseModel):
    """Position tracker tracking net quantity, cash flow, and MTM using Decimal precision."""
    position_id: str = Field(default_factory=lambda: f"POS_{uuid4().hex[:12]}")
    session_id: str
    symbol: str
    product_type: ProductType = ProductType.INTRADAY
    net_qty: int = 0
    buy_qty: int = 0
    sell_qty: int = 0
    total_buy_val: Decimal = Field(default_factory=lambda: Decimal("0.00"))
    total_sell_val: Decimal = Field(default_factory=lambda: Decimal("0.00"))
    avg_buy_price: Decimal = Field(default_factory=lambda: Decimal("0.00"))
    avg_sell_price: Decimal = Field(default_factory=lambda: Decimal("0.00"))
    current_ltp: Decimal = Field(default_factory=lambda: Decimal("0.00"))
    realized_pnl: Decimal = Field(default_factory=lambda: Decimal("0.00"))
    unrealized_pnl: Decimal = Field(default_factory=lambda: Decimal("0.00"))
    is_open: bool = False
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    def apply_fill(self, fill: Fill) -> None:
        """Update position from an executed fill report using Decimal arithmetic."""
        qty_dec = Decimal(fill.fill_qty)
        if fill.side == OrderSide.BUY:
            self.buy_qty += fill.fill_qty
            self.total_buy_val += fill.fill_price * qty_dec
            self.avg_buy_price = (self.total_buy_val / Decimal(self.buy_qty)).quantize(Decimal("0.0001"))
        else:
            self.sell_qty += fill.fill_qty
            self.total_sell_val += fill.fill_price * qty_dec
            self.avg_sell_price = (self.total_sell_val / Decimal(self.sell_qty)).quantize(Decimal("0.0001"))

        self.net_qty = self.buy_qty - self.sell_qty

        if self.opened_at is None and (self.buy_qty > 0 or self.sell_qty > 0):
            self.opened_at = fill.timestamp

        # Calculate realized P&L on closed portion
        closed_qty = min(self.buy_qty, self.sell_qty)
        if closed_qty > 0:
            self.realized_pnl = ((self.avg_sell_price - self.avg_buy_price) * Decimal(closed_qty)).quantize(Decimal("0.01"))

        self.is_open = self.net_qty != 0
        if not self.is_open and closed_qty > 0:
            self.closed_at = fill.timestamp

        self.update_ltp(fill.fill_price)

    def update_ltp(self, ltp: Decimal) -> None:
        """Recompute unrealized P&L using current last traded price."""
        self.current_ltp = ltp
        net_dec = Decimal(self.net_qty)
        if self.net_qty > 0:
            self.unrealized_pnl = ((ltp - self.avg_buy_price) * net_dec).quantize(Decimal("0.01"))
        elif self.net_qty < 0:
            self.unrealized_pnl = ((self.avg_sell_price - ltp) * abs(net_dec)).quantize(Decimal("0.01"))
        else:
            self.unrealized_pnl = Decimal("0.00")

    @property
    def total_pnl(self) -> Decimal:
        """Total P&L (realized + unrealized)."""
        return self.realized_pnl + self.unrealized_pnl


# ------------------------------------------------------------------------------
# 6. Risk State & Profit Protection
# ------------------------------------------------------------------------------

class ProfitProtectionState(BaseModel):
    """Dynamic profit-protection and trailing floor tracking state using Decimal."""
    is_activated: bool = False
    current_floor: Decimal = Decimal("0.00")
    peak_mtm: Decimal = Decimal("0.00")
    step_count: int = 0

    def evaluate(
        self,
        current_mtm: Decimal,
        trigger_profit: Decimal = Decimal("3000.00"),
        min_lock: Decimal = Decimal("500.00"),
        step_profit: Decimal = Decimal("1000.00"),
        step_lock: Decimal = Decimal("500.00"),
        mode: str = "lock_and_trail",
    ) -> bool:
        """
        Evaluate current MTM against profit protection rules.
        Returns True if profit floor is breached and an exit is required.
        """
        if current_mtm > self.peak_mtm:
            self.peak_mtm = current_mtm

        # Activate protection if threshold reached
        if not self.is_activated and self.peak_mtm >= trigger_profit:
            self.is_activated = True
            self.current_floor = min_lock

        # Dynamic trailing in lock_and_trail mode
        if self.is_activated and mode == "lock_and_trail":
            additional_profit = self.peak_mtm - trigger_profit
            if additional_profit >= step_profit:
                new_steps = int(additional_profit // step_profit)
                if new_steps > self.step_count:
                    self.step_count = new_steps
                    self.current_floor = min_lock + (Decimal(new_steps) * step_lock)

        # Breach condition: active and MTM retreats to or below floor
        if self.is_activated and current_mtm <= self.current_floor:
            return True

        return False


class DailyRiskState(BaseModel):
    """Real-time risk management state for the trading session using Decimal precision."""
    session_id: str
    trading_date: date
    max_loss_limit: Decimal = Decimal("3000.00")
    realized_pnl: Decimal = Decimal("0.00")
    unrealized_pnl: Decimal = Decimal("0.00")
    total_mtm: Decimal = Decimal("0.00")
    peak_mtm: Decimal = Decimal("0.00")
    is_kill_switch_active: bool = False
    kill_switch_reason: Optional[RiskTriggerReason] = None
    kill_switch_time: Optional[datetime] = None
    profit_protection: ProfitProtectionState = Field(default_factory=ProfitProtectionState)
    can_trade: bool = True

    def update_mtm(self, realized: Decimal, unrealized: Decimal) -> None:
        """Update MTM and check for hard loss threshold violation."""
        self.realized_pnl = realized
        self.unrealized_pnl = unrealized
        self.total_mtm = realized + unrealized

        if self.total_mtm > self.peak_mtm:
            self.peak_mtm = self.total_mtm

        # Check hard loss kill-switch: total_mtm <= -abs(max_loss_limit)
        if self.total_mtm <= -abs(self.max_loss_limit):
            self.trigger_kill_switch(RiskTriggerReason.DAILY_LOSS_LIMIT_BREACHED)

    def trigger_kill_switch(self, reason: RiskTriggerReason, timestamp: Optional[datetime] = None) -> None:
        """Activate hard kill-switch and disable further trading."""
        self.is_kill_switch_active = True
        self.can_trade = False
        self.kill_switch_reason = reason
        self.kill_switch_time = timestamp or now_utc()


# ------------------------------------------------------------------------------
# 7. Strategy Execution State Machine
# ------------------------------------------------------------------------------

class StrategyExecutionState(BaseModel):
    """Central state tracker for strategy instance."""
    session_id: str
    current_state: StrategyState = StrategyState.IDLE
    opening_range: Optional[OpeningRange] = None
    active_contract: Optional[OptionContract] = None
    active_position: Optional[Position] = None
    pending_client_order_id: Optional[str] = None
    last_signal: Optional[StrategySignal] = None
    updated_at: datetime = Field(default_factory=now_utc)

    def transition_to(self, target: StrategyState, timestamp: Optional[datetime] = None) -> None:
        """Transition strategy state with validation."""
        if not can_transition_strategy(self.current_state, target):
            raise InvalidStateTransitionError(
                entity=f"Strategy[{self.session_id}]",
                current_state=self.current_state.value,
                attempted_state=target.value,
            )
        self.current_state = target
        self.updated_at = timestamp or now_utc()


# ------------------------------------------------------------------------------
# 8. Audit Event & System Health
# ------------------------------------------------------------------------------

class AuditEvent(BaseModel):
    """Immutable audit record for regulatory traceability and post-trade debugging."""
    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: f"AUD_{uuid4().hex[:12]}")
    session_id: str
    timestamp: datetime = Field(default_factory=now_utc)
    event_type: AuditEventType
    component: str
    details: Dict[str, Any] = Field(default_factory=dict)


class SystemHealthState(BaseModel):
    """System health snapshot for watchdog monitoring."""
    model_config = ConfigDict(frozen=True)

    timestamp: datetime = Field(default_factory=now_utc)
    execution_mode: ExecutionMode
    database_connected: bool
    broker_connected: bool
    websocket_connected: bool
    open_orders_count: int = 0
    active_positions_count: int = 0
    memory_rss_mb: float = 0.0
    cpu_percent: float = 0.0
