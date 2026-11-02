"""SQLAlchemy declarative database schema with Decimal Numeric precision and comprehensive auditing."""

from datetime import datetime
from decimal import Decimal
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy entities."""
    pass


class StrategyConfigRecord(Base):
    """Database record for versioned strategy configurations."""
    __tablename__ = "strategy_configs"

    config_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    underlying_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("underlying_symbol", "version", name="uq_strategy_config_version"),
    )


class SessionRecord(Base):
    """Database record for trading session metadata."""
    __tablename__ = "trading_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trading_date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    orders: Mapped[list["OrderRecord"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    positions: Mapped[list["PositionRecord"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    risk_snapshots: Mapped[list["RiskSnapshotRecord"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    pnl_snapshots: Mapped[list["DailyPnlSnapshotRecord"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    audit_events: Mapped[list["AuditEventRecord"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class InstrumentRecord(Base):
    """Database record for trading instruments and resolved contracts."""
    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    exchange: Mapped[str] = mapped_column(String(16), default="NSE", nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(16), nullable=False)  # INDEX, OPTIDX
    underlying: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    strike: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=True)
    option_type: Mapped[str] = mapped_column(String(4), nullable=True)  # CE, PE
    expiry_date: Mapped[Date] = mapped_column(Date, nullable=True, index=True)
    lot_size: Mapped[int] = mapped_column(Integer, default=25, nullable=False)
    tick_size: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.05"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrderRecord(Base):
    """Database record for orders with Decimal pricing and state tracking."""
    __tablename__ = "orders"

    client_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    broker_order_id: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("trading_sessions.session_id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    side: Mapped[int] = mapped_column(Integer, nullable=False)  # 1: BUY, -1: SELL
    order_type: Mapped[int] = mapped_column(Integer, nullable=False)
    product_type: Mapped[str] = mapped_column(String(16), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    remaining_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    avg_fill_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0.0000"), nullable=False)
    limit_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0.0000"), nullable=False)
    tag: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status_message: Mapped[str] = mapped_column(String(255), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    session: Mapped["SessionRecord"] = relationship(back_populates="orders")
    fills: Mapped[list["FillRecord"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    transitions: Mapped[list["OrderStateTransitionRecord"]] = relationship(back_populates="order", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_orders_session_status", "session_id", "status"),
        Index("idx_orders_tag", "tag"),
    )


class OrderStateTransitionRecord(Base):
    """Audit log of order-state changes verifying valid state progression."""
    __tablename__ = "order_state_transitions"

    transition_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(64), ForeignKey("orders.client_order_id"), nullable=False)
    from_status: Mapped[int] = mapped_column(Integer, nullable=False)
    to_status: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    avg_fill_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0.0000"), nullable=False)
    message: Mapped[str] = mapped_column(String(255), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    order: Mapped["OrderRecord"] = relationship(back_populates="transitions")


class FillRecord(Base):
    """Database record for execution fills using Decimal fill price."""
    __tablename__ = "fills"

    fill_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(64), ForeignKey("orders.client_order_id"), nullable=False)
    broker_order_id: Mapped[str] = mapped_column(String(64), nullable=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[int] = mapped_column(Integer, nullable=False)
    fill_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    fill_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    order: Mapped["OrderRecord"] = relationship(back_populates="fills")


class PositionRecord(Base):
    """Database record for position status and realized/unrealized P&L using Decimal precision."""
    __tablename__ = "positions"

    position_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("trading_sessions.session_id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_type: Mapped[str] = mapped_column(String(16), nullable=False)
    net_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    buy_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sell_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_buy_val: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0.0000"), nullable=False)
    total_sell_val: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0.0000"), nullable=False)
    avg_buy_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0.0000"), nullable=False)
    avg_sell_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0.0000"), nullable=False)
    current_ltp: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0.0000"), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped["SessionRecord"] = relationship(back_populates="positions")

    __table_args__ = (
        Index("idx_positions_session_symbol", "session_id", "symbol"),
    )


class RiskSnapshotRecord(Base):
    """Periodic snapshot of strategy risk and MTM."""
    __tablename__ = "risk_snapshots"

    snapshot_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("trading_sessions.session_id"), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    total_mtm: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    peak_mtm: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    is_kill_switch_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    kill_switch_reason: Mapped[str] = mapped_column(String(64), nullable=True)
    is_profit_protection_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    profit_floor: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    session: Mapped["SessionRecord"] = relationship(back_populates="risk_snapshots")


class DailyPnlSnapshotRecord(Base):
    """End-of-day P&L summary and performance metrics snapshot."""
    __tablename__ = "daily_pnl_snapshots"

    pnl_snapshot_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("trading_sessions.session_id"), nullable=False)
    trading_date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    net_realized_pnl: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    net_unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    gross_mtm: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    peak_profit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    max_drawdown: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    total_trades_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    session: Mapped["SessionRecord"] = relationship(back_populates="pnl_snapshots")


class AuditEventRecord(Base):
    """Database record for system events, state transitions, and audit logs."""
    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("trading_sessions.session_id"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    component: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    session: Mapped["SessionRecord"] = relationship(back_populates="audit_events")


class BacktestRunRecord(Base):
    """Database record for completed backtest simulation runs."""
    __tablename__ = "backtest_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    start_date: Mapped[Date] = mapped_column(Date, nullable=False)
    end_date: Mapped[Date] = mapped_column(Date, nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy_config_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    execution_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    engine_version: Mapped[str] = mapped_column(String(16), default="1.0.0", nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    diagnostics_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    trades: Mapped[list["BacktestTradeRecord"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )


class BacktestTradeRecord(Base):
    """Database record for individual round-trip trades executed in a backtest."""
    __tablename__ = "backtest_trades"

    trade_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("backtest_runs.run_id"), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    underlying_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    option_type: Mapped[str] = mapped_column(String(4), nullable=False)
    strike: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), default="BUY", nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    exit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    gross_pnl: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    trading_costs: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    net_pnl: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(256), nullable=False)

    run: Mapped["BacktestRunRecord"] = relationship(back_populates="trades")

