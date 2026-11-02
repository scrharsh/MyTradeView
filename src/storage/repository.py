"""State persistence repository and trade journal data access layer using Decimal precision."""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import uuid4
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.common.constants import ExecutionMode, OrderStatus, SessionPhase
from src.core.models import (
    AuditEvent,
    BrokerOrder,
    DailyRiskState,
    Fill,
    Instrument,
    OptionContract,
    Position,
    StrategyConfigSnapshot,
    TradingSession,
)
from src.storage.schema import (
    AuditEventRecord,
    DailyPnlSnapshotRecord,
    FillRecord,
    InstrumentRecord,
    OrderRecord,
    OrderStateTransitionRecord,
    PositionRecord,
    RiskSnapshotRecord,
    SessionRecord,
    StrategyConfigRecord,
    BacktestRunRecord,
    BacktestTradeRecord,
)
from src.backtest.models import BacktestRun



class StateRepository:
    """Async repository providing ACID state storage, idempotency, and crash recovery."""

    def __init__(self, session: AsyncSession):
        self._session = session

    # --------------------------------------------------------------------------
    # 1. Strategy Configuration Versioning
    # --------------------------------------------------------------------------

    async def save_strategy_config(self, config_snapshot: StrategyConfigSnapshot, is_active: bool = True) -> None:
        """Persist versioned strategy configuration snapshot."""
        record = StrategyConfigRecord(
            config_id=config_snapshot.config_id,
            version=config_snapshot.version,
            underlying_symbol=config_snapshot.underlying_symbol,
            config_json=config_snapshot.model_dump(mode="json"),
            is_active=is_active,
            created_at=config_snapshot.created_at,
        )
        self._session.add(record)
        await self._session.flush()

    async def get_latest_active_config(self, underlying_symbol: str) -> Optional[StrategyConfigRecord]:
        """Fetch latest active strategy configuration."""
        stmt = (
            select(StrategyConfigRecord)
            .where(
                StrategyConfigRecord.underlying_symbol == underlying_symbol,
                StrategyConfigRecord.is_active.is_(True),
            )
            .order_by(StrategyConfigRecord.version.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # --------------------------------------------------------------------------
    # 2. Trading Session Lifecycle
    # --------------------------------------------------------------------------

    async def save_session(self, session_model: TradingSession) -> None:
        """Create trading session record."""
        record = SessionRecord(
            session_id=session_model.session_id,
            trading_date=session_model.trading_date,
            execution_mode=session_model.execution_mode.value,
            phase=session_model.phase.value,
            is_active=session_model.is_active,
            started_at=session_model.started_at,
            ended_at=session_model.ended_at,
        )
        self._session.add(record)
        await self._session.flush()

    async def get_session(self, session_id: str) -> Optional[TradingSession]:
        """Fetch session by ID."""
        stmt = select(SessionRecord).where(SessionRecord.session_id == session_id)
        result = await self._session.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            return None

        return TradingSession(
            session_id=record.session_id,
            trading_date=record.trading_date,
            execution_mode=ExecutionMode(record.execution_mode),
            phase=SessionPhase(record.phase),
            is_active=record.is_active,
            started_at=record.started_at,
            ended_at=record.ended_at,
        )

    async def update_session_phase(self, session_id: str, phase: SessionPhase) -> None:
        """Update trading session lifecycle phase."""
        stmt = (
            update(SessionRecord)
            .where(SessionRecord.session_id == session_id)
            .values(phase=phase.value)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    # --------------------------------------------------------------------------
    # 3. Instruments & Option Contracts
    # --------------------------------------------------------------------------

    async def save_or_update_instrument(self, contract: OptionContract, updated_at: datetime) -> None:
        """Upsert option contract instrument metadata."""
        stmt = select(InstrumentRecord).where(InstrumentRecord.symbol == contract.symbol)
        result = await self._session.execute(stmt)
        record = result.scalar_one_or_none()

        if record is None:
            record = InstrumentRecord(
                symbol=contract.symbol,
                exchange="NSE",
                instrument_type="OPTIDX",
                underlying=contract.underlying,
                strike=contract.strike,
                option_type=contract.option_type.value,
                expiry_date=contract.expiry_date,
                lot_size=contract.lot_size,
                tick_size=contract.tick_size,
                is_active=True,
                updated_at=updated_at,
            )
            self._session.add(record)
        else:
            record.strike = contract.strike
            record.expiry_date = contract.expiry_date
            record.lot_size = contract.lot_size
            record.updated_at = updated_at

        await self._session.flush()

    # --------------------------------------------------------------------------
    # 4. Orders, Transitions & Fills
    # --------------------------------------------------------------------------

    async def save_order(self, order: BrokerOrder, oms_status: Optional[str] = None) -> None:
        """Insert newly created order record."""
        oms_st = oms_status or getattr(order, "oms_status", None)
        if oms_st and hasattr(oms_st, "value"):
            oms_st = oms_st.value
        elif not oms_st:
            oms_st = "SUBMISSION_PENDING"

        record = OrderRecord(
            client_order_id=order.client_order_id,
            broker_order_id=order.broker_order_id,
            session_id=order.session_id,
            symbol=order.symbol,
            side=order.side.value,
            order_type=order.order_type.value,
            product_type=order.product_type.value,
            qty=order.qty,
            filled_qty=order.filled_qty,
            remaining_qty=order.remaining_qty,
            status=order.status.value,
            oms_status=str(oms_st),
            avg_fill_price=order.avg_fill_price,
            limit_price=order.limit_price,
            tag=order.tag,
            status_message=order.status_message,
            submitted_at=order.submitted_at,
            updated_at=order.updated_at,
        )
        self._session.add(record)
        await self._session.flush()

    async def record_order_transition(
        self,
        client_order_id: str,
        from_status: OrderStatus,
        to_status: OrderStatus,
        filled_qty: int,
        avg_price: Decimal,
        message: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Record state transition event for an order."""
        transition_record = OrderStateTransitionRecord(
            transition_id=f"TRANS_{uuid4().hex[:12]}",
            client_order_id=client_order_id,
            from_status=from_status.value,
            to_status=to_status.value,
            filled_qty=filled_qty,
            avg_fill_price=avg_price,
            message=message,
            timestamp=timestamp or datetime.now(),
        )
        self._session.add(transition_record)
        await self._session.flush()

    async def update_order_status(
        self,
        client_order_id: str,
        from_status: OrderStatus,
        to_status: OrderStatus,
        filled_qty: int,
        remaining_qty: int,
        avg_fill_price: Decimal,
        broker_order_id: Optional[str] = None,
        status_message: Optional[str] = None,
        updated_at: Optional[datetime] = None,
        oms_status: Optional[str] = None,
    ) -> None:
        """Update existing order status and log state transition."""
        ts = updated_at or datetime.now()
        values: dict = {
            "status": to_status.value,
            "filled_qty": filled_qty,
            "remaining_qty": remaining_qty,
            "avg_fill_price": avg_fill_price,
            "updated_at": ts,
        }
        if broker_order_id:
            values["broker_order_id"] = broker_order_id
        if status_message:
            values["status_message"] = status_message
        if oms_status:
            values["oms_status"] = str(oms_status)

        stmt = (
            update(OrderRecord)
            .where(OrderRecord.client_order_id == client_order_id)
            .values(**values)
        )
        await self._session.execute(stmt)

        # Audit order state transition
        await self.record_order_transition(
            client_order_id=client_order_id,
            from_status=from_status,
            to_status=to_status,
            filled_qty=filled_qty,
            avg_price=avg_fill_price,
            message=status_message,
            timestamp=ts,
        )
        await self._session.flush()

    async def record_fill(self, fill: Fill) -> None:
        """Insert fill record into the execution journal."""
        record = FillRecord(
            fill_id=fill.fill_id,
            client_order_id=fill.client_order_id,
            broker_order_id=fill.broker_order_id,
            symbol=fill.symbol,
            side=fill.side.value,
            fill_price=fill.fill_price,
            fill_qty=fill.fill_qty,
            timestamp=fill.timestamp,
        )
        self._session.add(record)
        await self._session.flush()

    async def get_order(self, client_order_id: str) -> Optional[OrderRecord]:
        """Fetch order record by client_order_id."""
        stmt = select(OrderRecord).where(OrderRecord.client_order_id == client_order_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_order_by_tag(self, tag: str) -> Optional[OrderRecord]:
        """Fetch order by client-side tag to prevent duplicate submissions."""
        stmt = select(OrderRecord).where(OrderRecord.tag == tag)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # --------------------------------------------------------------------------
    # 5. Position Management
    # --------------------------------------------------------------------------

    async def save_position(self, position: Position) -> None:
        """Alias for save_or_update_position."""
        await self.save_or_update_position(position)

    async def save_or_update_position(self, position: Position) -> None:
        """Upsert strategy position state."""
        stmt = select(PositionRecord).where(PositionRecord.position_id == position.position_id)
        result = await self._session.execute(stmt)
        record = result.scalar_one_or_none()

        if record is None:
            record = PositionRecord(
                position_id=position.position_id,
                session_id=position.session_id,
                symbol=position.symbol,
                product_type=position.product_type.value,
                net_qty=position.net_qty,
                buy_qty=position.buy_qty,
                sell_qty=position.sell_qty,
                total_buy_val=position.total_buy_val,
                total_sell_val=position.total_sell_val,
                avg_buy_price=position.avg_buy_price,
                avg_sell_price=position.avg_sell_price,
                current_ltp=position.current_ltp,
                realized_pnl=position.realized_pnl,
                unrealized_pnl=position.unrealized_pnl,
                is_open=position.is_open,
                opened_at=position.opened_at,
                closed_at=position.closed_at,
            )
            self._session.add(record)
        else:
            record.net_qty = position.net_qty
            record.buy_qty = position.buy_qty
            record.sell_qty = position.sell_qty
            record.total_buy_val = position.total_buy_val
            record.total_sell_val = position.total_sell_val
            record.avg_buy_price = position.avg_buy_price
            record.avg_sell_price = position.avg_sell_price
            record.current_ltp = position.current_ltp
            record.realized_pnl = position.realized_pnl
            record.unrealized_pnl = position.unrealized_pnl
            record.is_open = position.is_open
            record.opened_at = position.opened_at
            record.closed_at = position.closed_at

        await self._session.flush()

    async def get_open_positions(self, session_id: str) -> List[PositionRecord]:
        """Fetch all currently open positions for the session."""
        stmt = select(PositionRecord).where(
            PositionRecord.session_id == session_id,
            PositionRecord.is_open.is_(True),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # --------------------------------------------------------------------------
    # 6. Risk Snapshots & Daily P&L Snapshots
    # --------------------------------------------------------------------------

    async def record_risk_snapshot(self, state: DailyRiskState, timestamp: datetime) -> None:
        """Record periodic snapshot of strategy risk and MTM."""
        record = RiskSnapshotRecord(
            session_id=state.session_id,
            realized_pnl=state.realized_pnl,
            unrealized_pnl=state.unrealized_pnl,
            total_mtm=state.total_mtm,
            peak_mtm=state.peak_mtm,
            is_kill_switch_active=state.is_kill_switch_active,
            kill_switch_reason=state.kill_switch_reason.value if state.kill_switch_reason else None,
            is_profit_protection_active=state.profit_protection.is_activated,
            profit_floor=state.profit_protection.current_floor,
            timestamp=timestamp,
        )
        self._session.add(record)
        await self._session.flush()

    async def record_daily_pnl_snapshot(
        self,
        session_id: str,
        trading_date: date,
        net_realized_pnl: Decimal,
        net_unrealized_pnl: Decimal,
        gross_mtm: Decimal,
        peak_profit: Decimal,
        max_drawdown: Decimal,
        total_trades_count: int,
        timestamp: datetime,
    ) -> None:
        """Persist end-of-day summary P&L record."""
        record = DailyPnlSnapshotRecord(
            session_id=session_id,
            trading_date=trading_date,
            net_realized_pnl=net_realized_pnl,
            net_unrealized_pnl=net_unrealized_pnl,
            gross_mtm=gross_mtm,
            peak_profit=peak_profit,
            max_drawdown=max_drawdown,
            total_trades_count=total_trades_count,
            timestamp=timestamp,
        )
        self._session.add(record)
        await self._session.flush()

    # --------------------------------------------------------------------------
    # 7. Audit Trail
    # --------------------------------------------------------------------------

    async def record_audit_event(self, event: AuditEvent) -> None:
        """Persist immutable audit event without storing sensitive tokens or keys."""
        record = AuditEventRecord(
            event_id=event.event_id,
            session_id=event.session_id,
            timestamp=event.timestamp,
            event_type=event.event_type.value,
            component=event.component,
            details=event.details,
        )
        self._session.add(record)
        await self._session.flush()

    # --------------------------------------------------------------------------
    # 8. Backtest Persistence
    # --------------------------------------------------------------------------

    async def save_backtest_run(self, run: BacktestRun) -> None:
        """Persist authoritative backtest simulation run and all associated trade logs."""
        run_record = BacktestRunRecord(
            run_id=run.run_id,
            dataset_id=run.dataset_id,
            start_date=run.start_date,
            end_date=run.end_date,
            strategy_name=run.strategy_name,
            strategy_version=run.strategy_version,
            strategy_config_snapshot=run.strategy_config_snapshot,
            execution_config=run.execution_config.model_dump(mode="json"),
            engine_version=run.engine_version,
            metrics_json=run.metrics.model_dump(mode="json"),
            diagnostics_json=run.diagnostics.model_dump(mode="json"),
            created_at=run.created_at,
        )
        self._session.add(run_record)

        for trade in run.trades:
            trade_record = BacktestTradeRecord(
                trade_id=trade.trade_id,
                run_id=run.run_id,
                symbol=trade.symbol,
                underlying_symbol=trade.underlying_symbol,
                option_type=trade.option_type.value,
                strike=trade.strike,
                direction=trade.direction,
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                qty=trade.qty,
                lot_size=trade.lot_size,
                gross_pnl=trade.gross_pnl,
                trading_costs=trade.trading_costs,
                net_pnl=trade.net_pnl,
                exit_reason=trade.exit_reason,
            )
            self._session.add(trade_record)

        await self._session.flush()

    async def get_backtest_run(self, run_id: str) -> Optional[BacktestRunRecord]:
        """Fetch backtest run record with eager trades relationship."""
        stmt = (
            select(BacktestRunRecord)
            .options(selectinload(BacktestRunRecord.trades))
            .where(BacktestRunRecord.run_id == run_id)
        )
        res = await self._session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_backtest_runs(self, limit: int = 50) -> List[BacktestRunRecord]:
        """Fetch latest backtest runs ordered by created_at desc."""
        stmt = (
            select(BacktestRunRecord)
            .options(selectinload(BacktestRunRecord.trades))
            .order_by(BacktestRunRecord.created_at.desc())
            .limit(limit)
        )
        res = await self._session.execute(stmt)
        return list(res.scalars().all())

    # --------------------------------------------------------------------------
    # 7. Workstation API Query Helpers
    # --------------------------------------------------------------------------

    async def list_strategy_configs(self) -> List[StrategyConfigRecord]:
        """List all saved strategy configurations ordered by creation timestamp."""
        stmt = select(StrategyConfigRecord).order_by(StrategyConfigRecord.version.desc())
        res = await self._session.execute(stmt)
        return list(res.scalars().all())

    async def list_orders(self, session_id: Optional[str] = None, limit: int = 100) -> List[OrderRecord]:
        """List session or global orders with fills and transitions."""
        stmt = (
            select(OrderRecord)
            .options(selectinload(OrderRecord.fills), selectinload(OrderRecord.transitions))
            .order_by(OrderRecord.submitted_at.desc())
            .limit(limit)
        )
        if session_id:
            stmt = stmt.where(OrderRecord.session_id == session_id)
        res = await self._session.execute(stmt)
        return list(res.scalars().all())

    async def list_positions(self, session_id: Optional[str] = None) -> List[PositionRecord]:
        """List current session or all positions."""
        stmt = select(PositionRecord).order_by(PositionRecord.opened_at.desc())
        if session_id:
            stmt = stmt.where(PositionRecord.session_id == session_id)
        res = await self._session.execute(stmt)
        return list(res.scalars().all())

    async def list_instruments(self, is_active: bool = True) -> List[InstrumentRecord]:
        """List available instruments and resolved option contracts."""
        stmt = select(InstrumentRecord).where(InstrumentRecord.is_active == is_active).order_by(InstrumentRecord.strike.asc())
        res = await self._session.execute(stmt)
        return list(res.scalars().all())

    async def list_audit_events(self, session_id: Optional[str] = None, limit: int = 100) -> List[AuditEventRecord]:
        """List immutable audit log records."""
        stmt = select(AuditEventRecord).order_by(AuditEventRecord.timestamp.desc()).limit(limit)
        if session_id:
            stmt = stmt.where(AuditEventRecord.session_id == session_id)
        res = await self._session.execute(stmt)
        return list(res.scalars().all())


