"""SQLAlchemy declarative database schema for persistent trade state and audit trail."""

from datetime import datetime
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy entities."""
    pass


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
    audit_events: Mapped[list["AuditEventRecord"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class OrderRecord(Base):
    """Database record for all client and broker orders."""
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
    avg_fill_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    limit_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tag: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status_message: Mapped[str] = mapped_column(String(255), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    session: Mapped["SessionRecord"] = relationship(back_populates="orders")
    fills: Mapped[list["FillRecord"]] = relationship(back_populates="order", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_orders_session_status", "session_id", "status"),
        Index("idx_orders_tag", "tag"),
    )


class FillRecord(Base):
    """Database record for execution fills."""
    __tablename__ = "fills"

    fill_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(64), ForeignKey("orders.client_order_id"), nullable=False)
    broker_order_id: Mapped[str] = mapped_column(String(64), nullable=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[int] = mapped_column(Integer, nullable=False)
    fill_price: Mapped[float] = mapped_column(Float, nullable=False)
    fill_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    order: Mapped["OrderRecord"] = relationship(back_populates="fills")


class PositionRecord(Base):
    """Database record for position status and realized/unrealized P&L."""
    __tablename__ = "positions"

    position_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("trading_sessions.session_id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_type: Mapped[str] = mapped_column(String(16), nullable=False)
    net_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    buy_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sell_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_buy_val: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_sell_val: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    avg_buy_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    avg_sell_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    current_ltp: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped["SessionRecord"] = relationship(back_populates="positions")

    __table_args__ = (
        Index("idx_positions_session_symbol", "session_id", "symbol"),
    )


class DailyMetricsRecord(Base):
    """Database record for periodic snapshots of daily strategy risk and MTM."""
    __tablename__ = "daily_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trading_date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_mtm: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    peak_mtm: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_kill_switch_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    kill_switch_reason: Mapped[str] = mapped_column(String(64), nullable=True)
    is_profit_protection_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    profit_floor: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
