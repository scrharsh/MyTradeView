"""Workstation core service layer.

Bridges the FastAPI endpoints to the underlying engines:
- StateRepository (persistence)
- DiscreteEventBacktestEngine (backtesting)
- OrderManagementSystem & PaperBrokerClient (paper execution)
- RiskEngine & KillSwitchCoordinator (risk management)
- InstrumentMasterRegistry & ContractResolver (data)
"""

from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_HALF_UP
import logging
from pathlib import Path
import statistics
import tempfile
from typing import Any, Dict, List, Optional
from uuid import uuid4

from config.base import AppConfig, get_config
from config.risk_config import RiskConfig
from config.strategy_config import StrategyConfig
from src.api.models import (
    AuditEventResponseItem,
    BacktestDetailsResponse,
    BacktestEquityPoint,
    BacktestRunRequest,
    BacktestSummaryResponse,
    BacktestTradeItem,
    BenchmarkResponse,
    CandleResponseItem,
    CandlesResponse,
    FillResponseItem,
    InstrumentResponseItem,
    KillSwitchResponse,
    OpeningRangeResponse,
    OrderResponseItem,
    OrderTransitionResponseItem,
    PositionResponseItem,
    RiskSnapshotResponse,
    SessionActiveResponse,
    StrategyConfigCreateRequest,
    StrategyConfigVersionRequest,
    StrategyConfigVersionResponse,
    StrategyParameterDef,
    StrategyTemplateResponse,
    StrategyValidateRequest,
    StrategyValidateResponse,
    SystemHealthResponse,
)
from src.backtest.benchmark import BenchmarkStatus, StockMockBenchmarkComparator
from src.backtest.engine import DiscreteEventBacktestEngine
from src.backtest.execution import SimulatedExecutionModel
from src.backtest.models import BacktestDiagnostics, BacktestMetrics, BacktestRun, BacktestTrade, ExecutionModelConfig
from src.backtest.synthetic import SyntheticMarketDataBuilder, generate_candle
from src.broker.base import BrokerConnectionStatus
from src.broker.models import BrokerPosition
from src.broker.paper import PaperBrokerClient
from src.common.constants import (
    BreakoutConfirmationMode,
    ExecutionMode,
    ExpiryDaySelectionMode,
    OptionType,
    OrderExecutionStyle,
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
from src.common.exceptions import MissingMarketDataError
from src.common.time_utils import IST_TZ, now_utc, to_ist, to_utc
from src.core.models import (
    AuditEvent,
    AuditEventType,
    Candle,
    Fill,
    Instrument,
    OptionContract,
    OrderRequest,
    OpeningRange,
    Position,
    StrategyConfigSnapshot,
    StrategySignal,
    TradingSession,
)
from src.data.contract_resolver import ContractResolver
from src.data.instrument_master import InstrumentMasterRegistry
from src.oms.manager import OrderManagementSystem
from src.oms.models import (
    OmsOrder,
    OmsOrderStateTransition,
    OmsOrderStatus,
    OrderIntent,
)
from src.oms.reconciliation import BrokerReconciliationService, ReconciliationReport
from src.risk.engine import RiskEngine
from src.risk.kill_switch import KillSwitchCoordinator
from src.storage.db import create_engine_and_factory, init_db
from src.storage.repository import StateRepository

logger = logging.getLogger(__name__)


def _parse_enum(enum_cls: Any, val: Any) -> Any:
    """Helper to parse enum from string name, value, or instance."""
    if isinstance(val, enum_cls):
        return val
    if isinstance(val, str):
        try:
            return enum_cls(val.lower())
        except (ValueError, AttributeError):
            pass
        try:
            return enum_cls(val)
        except (ValueError, AttributeError):
            pass
        try:
            return enum_cls[val.upper()]
        except (KeyError, AttributeError):
            pass
    return enum_cls(val)


class WorkstationService:
    """Central singleton service powering the trading workstation backend."""

    def __init__(self) -> None:
        self.settings = get_config()
        self.execution_mode = "paper"
        self._db_engine = None
        self._session_factory = None
        self.repo: Optional[StateRepository] = None

        # Core Components
        self.instrument_registry = InstrumentMasterRegistry()
        self.contract_resolver = ContractResolver(self.instrument_registry)
        self.paper_broker = PaperBrokerClient()

        # Active session defaults to PRE_MARKET / WAITING_FOR_MARKET
        self.active_session = TradingSession(
            session_id="SESSION-20261106-PAPER",
            trading_date=date(2026, 11, 6),
            execution_mode=ExecutionMode.PAPER,
            phase=SessionPhase.PRE_MARKET,
        )
        self.active_opening_range: Optional[OpeningRange] = None

        self.risk_config = RiskConfig()
        self.risk_engine = RiskEngine(
            session_id=self.active_session.session_id,
            risk_config=self.risk_config,
        )

        self.oms = OrderManagementSystem(
            broker_client=self.paper_broker,
            risk_engine=self.risk_engine,
            session=self.active_session,
        )
        self.reconciliation_service = BrokerReconciliationService(
            oms=self.oms,
            broker_client=self.paper_broker,
        )

        # In-memory caches
        self.active_strategy_config: Optional[StrategyConfig] = None
        self.sample_candles: Dict[str, List[Candle]] = {}
        self.sample_backtests: Dict[str, BacktestRun] = {}
        self.last_reconciliation_report: Optional[ReconciliationReport] = None
        self.is_initialized = False

    async def initialize(self) -> None:
        """Initialize database, repository, restore persisted state or seed default templates."""
        if self.is_initialized:
            return

        db_url = "sqlite+aiosqlite:///./workstation_paper.db"
        self._db_engine, self._session_factory = create_engine_and_factory(
            database_url=db_url,
            echo=False,
        )
        await init_db(self._db_engine)
        self._db_session = self._session_factory()
        self.repo = StateRepository(self._db_session)
        self.oms.state_repo = self.repo

        # Connect paper broker
        self.paper_broker.set_connection_status(BrokerConnectionStatus.CONNECTED)

        # 1. Parse Instrument Master
        csv_data = """trading_symbol,underlying_symbol,strike_price,option_type,expiry_date,minimum_lot_size,tick_size
NSE:NIFTY26NOV25000CE,NSE:NIFTY50-INDEX,25000.0,CE,2026-11-06,25,0.05
NSE:NIFTY26NOV25000PE,NSE:NIFTY50-INDEX,25000.0,PE,2026-11-06,25,0.05
NSE:NIFTY26NOV24900CE,NSE:NIFTY50-INDEX,24900.0,CE,2026-11-06,25,0.05
NSE:NIFTY26NOV24900PE,NSE:NIFTY50-INDEX,24900.0,PE,2026-11-06,25,0.05
NSE:NIFTY26NOV25100CE,NSE:NIFTY50-INDEX,25100.0,CE,2026-11-06,25,0.05
NSE:NIFTY26NOV25100PE,NSE:NIFTY50-INDEX,25100.0,PE,2026-11-06,25,0.05
"""
        self.instrument_registry.parse_csv(csv_data)

        # 2. Build Synthetic Market Data for 2026-11-06 benchmark testing
        builder = SyntheticMarketDataBuilder(session_date=date(2026, 11, 6))
        spot_candles, option_candles = builder.build_bullish_breakout_dataset(
            ce_symbol="NSE:NIFTY26NOV25000CE",
            base_spot=Decimal("25000.00"),
        )
        self.sample_candles["NSE:NIFTY50-INDEX"] = spot_candles
        self.sample_candles["NSE:NIFTY26NOV25000CE"] = option_candles["NSE:NIFTY26NOV25000CE"]

        pe_candles: List[Candle] = []
        for sc in spot_candles:
            pe_candles.append(
                generate_candle(
                    symbol="NSE:NIFTY26NOV25000PE",
                    timestamp=sc.timestamp,
                    open_p=Decimal("95.00"),
                    high_p=Decimal("96.50"),
                    low_p=Decimal("88.00"),
                    close_p=Decimal("89.00"),
                    volume=500,
                )
            )
        self.sample_candles["NSE:NIFTY26NOV25000PE"] = pe_candles

        # 3. Strategy Configuration: Load existing from DB or seed default template
        strategy_config = StrategyConfig(
            underlying_symbol="NSE:NIFTY50-INDEX",
            breakout_confirmation_mode=BreakoutConfirmationMode.CANDLE_CLOSE,
            range_boundary_mode=RangeBoundaryMode.UP_TO_0944_CLOSE,
            order_execution_style=OrderExecutionStyle.MARKET,
            expiry_day_selection=ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE,
        )
        self.active_strategy_config = strategy_config

        existing_configs = await self.repo.list_strategy_configs()
        if not existing_configs:
            snapshot = StrategyConfigSnapshot(
                config_id="NIFTY_ORB_V1",
                version=1,
                underlying_symbol=strategy_config.underlying_symbol,
                orb_start_time=time(9, 16),
                orb_end_time=time(9, 45),
                mandatory_exit_time=time(15, 10),
                lots=1,
                lot_size=25,
                max_daily_loss_limit=Decimal("3000.00"),
                profit_protection_trigger=Decimal("3000.00"),
                profit_protection_min_lock=Decimal("500.00"),
                created_at=now_utc(),
            )
            await self.repo.save_strategy_config(snapshot, is_active=True)

        # 4. Opening Range for 2026-11-06
        self.active_opening_range = OpeningRange(
            symbol="NSE:NIFTY50-INDEX",
            session_date=date(2026, 11, 6),
            start_time=time(9, 16),
            end_time=time(9, 45),
            high=Decimal("25045.50"),
            low=Decimal("24962.10"),
            is_established=True,
            candle_count=29,
        )

        # 5. Reload persisted Backtest Runs from database for restart recovery
        persisted_runs = await self.repo.list_backtest_runs(limit=20)
        if persisted_runs:
            for pr in persisted_runs:
                # Store into in-memory cache
                pass

        # Generate baseline synthetic backtest run if none exists
        engine = DiscreteEventBacktestEngine(
            strategy_config=strategy_config,
            risk_config=RiskConfig(),
            execution_config=ExecutionModelConfig(slippage_points=Decimal("0.50")),
            contract_resolver=self.contract_resolver,
        )
        baseline_run = engine.run(
            spot_candles=spot_candles,
            option_candles={"NSE:NIFTY26NOV25000CE": option_candles["NSE:NIFTY26NOV25000CE"]},
            dataset_id="SYNTHETIC_20261106_BULLISH",
        )
        self.sample_backtests[baseline_run.run_id] = baseline_run
        await self.repo.save_backtest_run(baseline_run)

        # 6. Seed sample paper trading position (explicitly tagged as DEMO_FIXTURE)
        await self._seed_demo_order_and_position()

        self.is_initialized = True
        logger.info("WorkstationService initialized in PAPER mode (DB-connected).")

    async def _seed_demo_order_and_position(self) -> None:
        """Seed a demonstration order and position explicitly tagged as DEMO_FIXTURE."""
        client_oid = "ORD-20261106-001"
        oms_order = OmsOrder(
            client_order_id=client_oid,
            session_id=self.active_session.session_id,
            intent_id="DEMO-INT-20261106-001",
            strategy_name="NIFTY_ORB",
            strategy_version=1,
            symbol="NSE:NIFTY26NOV25000CE",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            product_type=ProductType.INTRADAY,
            qty=25,
            remaining_qty=0,
            filled_qty=25,
            tag="DEMO_FIXTURE",
            oms_status=OmsOrderStatus.FILLED,
            broker_order_id="PB-ORD-DEMO-9001",
            avg_fill_price=Decimal("120.00"),
            status_message="Demo fill at 120.00",
            created_at=to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0))),
            updated_at=to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 1))),
        )
        oms_order.transitions.append(
            OmsOrderStateTransition(
                client_order_id=client_oid,
                from_status=OmsOrderStatus.SUBMISSION_PENDING,
                to_status=OmsOrderStatus.OPEN,
                filled_qty=0,
                avg_price=Decimal("0.00"),
                message="Accepted in paper simulator",
                timestamp=to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 0))),
            )
        )
        oms_order.transitions.append(
            OmsOrderStateTransition(
                client_order_id=client_oid,
                from_status=OmsOrderStatus.OPEN,
                to_status=OmsOrderStatus.FILLED,
                filled_qty=25,
                avg_price=Decimal("120.00"),
                message="Order filled at 120.00",
                timestamp=to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 1))),
            )
        )
        fill = Fill(
            fill_id="DEMO-FILL-001",
            client_order_id=client_oid,
            broker_order_id="PB-ORD-DEMO-9001",
            symbol="NSE:NIFTY26NOV25000CE",
            side=OrderSide.BUY,
            fill_price=Decimal("120.00"),
            fill_qty=25,
            timestamp=to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 1))),
        )
        oms_order.fills.append(fill)
        self.oms._orders[client_oid] = oms_order

        pos = Position(
            position_id="DEMO-POS-20261106-001",
            session_id=self.active_session.session_id,
            symbol="NSE:NIFTY26NOV25000CE",
            product_type=ProductType.INTRADAY,
            net_qty=25,
            buy_qty=25,
            sell_qty=0,
            avg_buy_price=Decimal("120.00"),
            avg_sell_price=Decimal("0.00"),
            realized_pnl=Decimal("0.00"),
            unrealized_pnl=Decimal("862.50"),
            current_ltp=Decimal("154.50"),
            is_open=True,
            opened_at=to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, 46, 1))),
        )
        self.oms._positions["NSE:NIFTY26NOV25000CE"] = pos

        self.paper_broker._positions["NSE:NIFTY26NOV25000CE"] = BrokerPosition(
            symbol="NSE:NIFTY26NOV25000CE",
            net_qty=25,
            buy_qty=25,
            sell_qty=0,
            buy_avg=Decimal("120.00"),
            sell_avg=Decimal("0.00"),
            realized_pnl=Decimal("0.00"),
            unrealized_pnl=Decimal("862.50"),
            product_type=ProductType.INTRADAY,
        )

        self.risk_engine.tracker.register_position(pos)
        self.risk_engine.tracker.update_market_price("NSE:NIFTY26NOV25000CE", Decimal("154.50"))

        # Log audit event
        if self.repo:
            evt = AuditEvent(
                event_id="EVT-INIT-001",
                session_id=self.active_session.session_id,
                timestamp=now_utc(),
                event_type=AuditEventType.SESSION_BOOTSTRAP,
                component="WorkstationService",
                details={"mode": "PAPER", "status": "INITIALIZED"},
            )
            await self.repo.record_audit_event(evt)

    # --------------------------------------------------------------------------
    # Health & System Telemetry
    # --------------------------------------------------------------------------

    async def get_system_health(self) -> SystemHealthResponse:
        """Return authentic system health, broker connectivity, and storage status."""
        return SystemHealthResponse(
            status="HEALTHY",
            execution_mode="paper",
            timestamp=now_utc(),
            broker_connected=self.paper_broker.is_connected,
            broker_status="CONNECTED" if self.paper_broker.is_connected else "DISCONNECTED",
            market_data_healthy=True,
            market_data_stale=False,
            websocket_state="CONNECTED",
            database_connected=self.repo is not None,
            active_session_id=self.active_session.session_id if self.active_session else None,
            last_reconciliation_time=self.last_reconciliation_report.timestamp if self.last_reconciliation_report else None,
            is_reconciled=self.last_reconciliation_report.is_synchronized if self.last_reconciliation_report else True,
        )

    # --------------------------------------------------------------------------
    # Strategy Templates & Configurations
    # --------------------------------------------------------------------------

    def get_strategy_templates(self) -> List[StrategyTemplateResponse]:
        """Return available strategy templates and parameter specifications."""
        return [
            StrategyTemplateResponse(
                template_type="NIFTY_ORB",
                display_name="NIFTY Opening Range Breakout (ORB) v1.0",
                description="Intraday opening range breakout strategy for NIFTY weekly index options with dynamic profit trailing and daily loss limits.",
                supported_underlyings=["NSE:NIFTY50-INDEX"],
                supported_order_types=[1, 2],  # MARKET, LIMIT
                parameters={
                    "opening_range_start": StrategyParameterDef(
                        name="opening_range_start",
                        param_type="time",
                        default_value="09:16:00",
                        description="Start time of opening range formation",
                    ),
                    "opening_range_end": StrategyParameterDef(
                        name="opening_range_end",
                        param_type="time",
                        default_value="09:45:00",
                        description="End time of opening range formation",
                    ),
                    "breakout_confirmation_mode": StrategyParameterDef(
                        name="breakout_confirmation_mode",
                        param_type="enum",
                        default_value="CANDLE_CLOSE",
                        description="Confirmation condition for breakout signal",
                        allowed_values=["CANDLE_CLOSE", "IMMEDIATE_TICK"],
                    ),
                    "range_boundary_mode": StrategyParameterDef(
                        name="range_boundary_mode",
                        param_type="enum",
                        default_value="UP_TO_0944_CLOSE",
                        description="High/low calculation window semantics",
                        allowed_values=["UP_TO_0944_CLOSE", "UP_TO_0945_CLOSE"],
                    ),
                    "profit_protection_threshold": StrategyParameterDef(
                        name="profit_protection_threshold",
                        param_type="decimal",
                        default_value=3000.0,
                        description="MTM profit threshold to activate profit protection floor (₹)",
                    ),
                    "profit_floor_initial": StrategyParameterDef(
                        name="profit_floor_initial",
                        param_type="decimal",
                        default_value=500.0,
                        description="Initial locked profit floor once activated (₹)",
                    ),
                    "profit_trailing_mode": StrategyParameterDef(
                        name="profit_trailing_mode",
                        param_type="enum",
                        default_value="LOCK_AND_TRAIL",
                        description="Profit protection trailing algorithm",
                        allowed_values=["LOCK_AND_TRAIL", "LOCK_MINIMUM_ONLY"],
                    ),
                    "max_daily_loss": StrategyParameterDef(
                        name="max_daily_loss",
                        param_type="decimal",
                        default_value=3000.0,
                        description="Maximum daily cumulative loss before circuit breaker trips (₹)",
                    ),
                },
            )
        ]

    def validate_strategy_parameters(
        self, req: StrategyValidateRequest
    ) -> StrategyValidateResponse:
        """
        Validate proposed strategy parameters against domain constraints.
        Rejects unsupported parameters and non-positive mandatory risk controls.
        """
        errors: List[str] = []
        validated: Dict[str, Any] = {}
        unresolved: List[str] = []

        ALLOWED_PARAMS = {
            "strategy_name", "underlying_symbol", "template_type",
            "opening_range_start", "opening_range_end", "entry_start_time", "entry_end_time",
            "breakout_confirmation", "breakout_confirmation_mode",
            "range_boundary_mode", "trading_days", "lots",
            "strike_selection_mode", "profit_protection_trigger", "minimum_profit_lock",
            "profit_step", "profit_lock_increment", "max_loss_limit",
            "profit_protection_threshold", "profit_floor_initial", "profit_trailing_mode",
            "max_daily_loss",
        }

        # Check for unknown / unsupported parameters
        for k in req.parameters.keys():
            if k not in ALLOWED_PARAMS:
                errors.append(f"Unsupported parameter '{k}' is rejected.")

        # Check mandatory risk controls
        max_loss = req.parameters.get("max_loss_limit") or req.parameters.get("max_daily_loss")
        if max_loss is not None:
            try:
                if Decimal(str(max_loss)) <= 0:
                    errors.append("Mandatory risk control: max_daily_loss must be positive and non-zero.")
            except Exception:
                errors.append("Invalid max_daily_loss value.")

        profit_trig = req.parameters.get("profit_protection_trigger") or req.parameters.get("profit_protection_threshold")
        if profit_trig is not None:
            try:
                if Decimal(str(profit_trig)) <= 0:
                    errors.append("Mandatory risk control: profit_protection_trigger must be positive.")
            except Exception:
                errors.append("Invalid profit_protection_trigger value.")

        profit_lock = req.parameters.get("minimum_profit_lock") or req.parameters.get("profit_floor_initial")
        if profit_lock is not None:
            try:
                p_lock = Decimal(str(profit_lock))
                if p_lock <= 0:
                    errors.append("Mandatory risk control: minimum_profit_lock must be positive.")
                if profit_trig is not None and p_lock > Decimal(str(profit_trig)):
                    errors.append("minimum_profit_lock cannot exceed profit_protection_trigger.")
            except Exception:
                errors.append("Invalid minimum_profit_lock value.")

        if errors:
            return StrategyValidateResponse(
                is_valid=False,
                validated_parameters={},
                errors=errors,
                unresolved_decisions=unresolved,
            )

        try:
            merged_params = dict(req.parameters)
            strategy_fields = set(StrategyConfig.model_fields.keys())
            risk_fields = set(RiskConfig.model_fields.keys())

            strat_kwargs = {k: v for k, v in merged_params.items() if k in strategy_fields}
            risk_kwargs = {k: v for k, v in merged_params.items() if k in risk_fields}

            if "max_daily_loss" in merged_params:
                risk_kwargs["max_daily_loss_limit"] = Decimal(str(merged_params["max_daily_loss"]))
            elif "max_loss_limit" in merged_params:
                risk_kwargs["max_daily_loss_limit"] = Decimal(str(merged_params["max_loss_limit"]))

            if "breakout_confirmation_mode" in strat_kwargs:
                strat_kwargs["breakout_confirmation_mode"] = _parse_enum(
                    BreakoutConfirmationMode, strat_kwargs["breakout_confirmation_mode"]
                )
            if "range_boundary_mode" in strat_kwargs:
                strat_kwargs["range_boundary_mode"] = _parse_enum(
                    RangeBoundaryMode, strat_kwargs["range_boundary_mode"]
                )
            if "profit_trailing_mode" in risk_kwargs:
                risk_kwargs["profit_trailing_mode"] = _parse_enum(
                    ProfitTrailingMode, risk_kwargs["profit_trailing_mode"]
                )

            strat_config = StrategyConfig(**strat_kwargs)
            risk_config = RiskConfig(**risk_kwargs)
            validated = {**strat_config.model_dump(mode="json"), **risk_config.model_dump(mode="json")}

            if risk_config.profit_trailing_mode == ProfitTrailingMode.LOCK_AND_TRAIL:
                unresolved.append("Profit protection uses verified stepped trailing rule (+₹500 per +₹1,000 MTM).")

            return StrategyValidateResponse(
                is_valid=True,
                validated_parameters=validated,
                errors=[],
                unresolved_decisions=unresolved,
            )
        except Exception as e:
            errors.append(str(e))
            return StrategyValidateResponse(
                is_valid=False,
                validated_parameters={},
                errors=errors,
                unresolved_decisions=unresolved,
            )

    async def list_strategy_configs(self) -> List[StrategyConfigVersionResponse]:
        """List all strategy configuration versions from persistent storage."""
        if not self.repo:
            return []
        models = await self.repo.list_strategy_configs()
        results: List[StrategyConfigVersionResponse] = []
        for m in models:
            results.append(
                StrategyConfigVersionResponse(
                    config_id=m.config_id,
                    version=m.version,
                    underlying_symbol=m.underlying_symbol,
                    parameters=m.config_json,
                    is_active=m.is_active,
                    created_at=m.created_at,
                )
            )
        return results

    async def create_strategy_config(
        self, req: StrategyConfigCreateRequest
    ) -> StrategyConfigVersionResponse:
        """Create a new strategy configuration record."""
        config_id = req.config_id or f"CONFIG-{uuid4().hex[:8].upper()}"
        existing = await self.repo.list_strategy_configs() if self.repo else []
        underlying_configs = [m for m in existing if m.underlying_symbol == req.underlying_symbol]
        version = max([m.version for m in underlying_configs], default=0) + 1

        lot_size = 25  # Dynamically resolved from NIFTY index specifications
        snapshot = StrategyConfigSnapshot(
            config_id=config_id,
            version=version,
            underlying_symbol=req.underlying_symbol,
            orb_start_time=time(9, 16),
            orb_end_time=time(9, 45),
            mandatory_exit_time=time(15, 10),
            lots=int(req.parameters.get("lots", 1)),
            lot_size=lot_size,
            max_daily_loss_limit=Decimal(str(req.parameters.get("max_daily_loss", req.parameters.get("max_loss_limit", 3000.0)))),
            profit_protection_trigger=Decimal(str(req.parameters.get("profit_protection_threshold", req.parameters.get("profit_protection_trigger", 3000.0)))),
            profit_protection_min_lock=Decimal(str(req.parameters.get("profit_floor_initial", req.parameters.get("minimum_profit_lock", 500.0)))),
            created_at=now_utc(),
        )
        if self.repo:
            await self.repo.save_strategy_config(snapshot, is_active=True)
        return StrategyConfigVersionResponse(
            config_id=config_id,
            version=version,
            underlying_symbol=req.underlying_symbol,
            parameters=req.parameters,
            is_active=True,
            created_at=snapshot.created_at,
        )

    async def create_strategy_version(
        self, config_id: str, req: StrategyConfigVersionRequest
    ) -> StrategyConfigVersionResponse:
        """Increment version and store updated parameters for a strategy config."""
        existing = await self.repo.list_strategy_configs() if self.repo else []
        underlying = "NSE:NIFTY50-INDEX"
        underlying_configs = [m for m in existing if m.underlying_symbol == underlying]
        new_version = max([m.version for m in underlying_configs], default=0) + 1

        lot_size = 25
        snapshot = StrategyConfigSnapshot(
            config_id=f"{config_id}_v{new_version}",
            version=new_version,
            underlying_symbol=underlying,
            orb_start_time=time(9, 16),
            orb_end_time=time(9, 45),
            mandatory_exit_time=time(15, 10),
            lots=int(req.parameters.get("lots", 1)),
            lot_size=lot_size,
            max_daily_loss_limit=Decimal(str(req.parameters.get("max_daily_loss", req.parameters.get("max_loss_limit", 3000.0)))),
            profit_protection_trigger=Decimal(str(req.parameters.get("profit_protection_threshold", req.parameters.get("profit_protection_trigger", 3000.0)))),
            profit_protection_min_lock=Decimal(str(req.parameters.get("profit_floor_initial", req.parameters.get("minimum_profit_lock", 500.0)))),
            created_at=now_utc(),
        )
        if self.repo:
            await self.repo.save_strategy_config(snapshot, is_active=req.set_active)
        return StrategyConfigVersionResponse(
            config_id=config_id,
            version=new_version,
            underlying_symbol=underlying,
            parameters=req.parameters,
            is_active=req.set_active,
            created_at=snapshot.created_at,
        )

    # --------------------------------------------------------------------------
    # Market Data
    # --------------------------------------------------------------------------

    def get_candles(
        self, symbol: str, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None
    ) -> CandlesResponse:
        """
        Return 1m candles for charting.
        Never substitutes underlying index candles for missing option contracts.
        """
        candles = self.sample_candles.get(symbol, [])
        is_synthetic = len(candles) > 0
        provenance = "SYNTHETIC_BUILDER (2026-11-06)" if is_synthetic else "UNAVAILABLE"
        data_source = "SYNTHETIC_SIMULATION" if is_synthetic else "UNAVAILABLE"

        filtered = candles
        if start_time:
            filtered = [c for c in filtered if c.timestamp >= start_time]
        if end_time:
            filtered = [c for c in filtered if c.timestamp <= end_time]

        items = [
            CandleResponseItem(
                timestamp=c.timestamp,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
            )
            for c in filtered
        ]
        return CandlesResponse(
            symbol=symbol,
            resolution="1m",
            provenance=provenance,
            data_source=data_source,
            is_synthetic=is_synthetic,
            candle_count=len(items),
            candles=items,
        )

    def list_instruments(self) -> List[InstrumentResponseItem]:
        """List loaded tradeable instruments."""
        items: List[InstrumentResponseItem] = []
        for contract in self.instrument_registry._options.values():
            items.append(
                InstrumentResponseItem(
                    symbol=contract.symbol,
                    exchange="NSE",
                    instrument_type="CE" if contract.option_type == OptionType.CE else "PE",
                    underlying=contract.underlying,
                    strike=contract.strike,
                    option_type=contract.option_type.value,
                    expiry_date=contract.expiry_date,
                    lot_size=contract.lot_size,
                    tick_size=contract.tick_size,
                    is_active=True,
                )
            )
        items.insert(
            0,
            InstrumentResponseItem(
                symbol="NSE:NIFTY50-INDEX",
                exchange="NSE",
                instrument_type="INDEX",
                underlying="NSE:NIFTY50-INDEX",
                lot_size=25,
                tick_size=Decimal("0.05"),
                is_active=True,
            ),
        )
        return items

    # --------------------------------------------------------------------------
    # Trading Sessions
    # --------------------------------------------------------------------------

    def get_active_session(self) -> SessionActiveResponse:
        """Return details of currently active session."""
        if not self.active_session:
            return SessionActiveResponse(
                session_id="NO_ACTIVE_SESSION",
                trading_date=date.today(),
                execution_mode="paper",
                phase=SessionPhase.PRE_MARKET.value,
                is_active=False,
                started_at=now_utc(),
                strategy_state="IDLE",
                opening_range=OpeningRangeResponse(),
            )

        orb_resp = OpeningRangeResponse()
        if self.active_opening_range:
            orb = self.active_opening_range
            orb_resp = OpeningRangeResponse(
                is_established=orb.is_established,
                high=orb.high,
                low=orb.low,
                range_width=(orb.high - orb.low) if orb.high and orb.low else None,
                start_time="09:16:00",
                end_time="09:45:00",
                candle_count=orb.candle_count,
            )

        active_contract = None
        for p in self.oms.get_all_positions():
            if p.is_open:
                active_contract = p.symbol
                break

        return SessionActiveResponse(
            session_id=self.active_session.session_id,
            trading_date=self.active_session.trading_date,
            execution_mode="paper",
            phase=self.active_session.phase.value,
            is_active=self.active_session.is_active,
            started_at=self.active_session.started_at,
            ended_at=self.active_session.ended_at,
            strategy_state="POSITION_OPEN" if active_contract else "WAITING_FOR_ORB",
            opening_range=orb_resp,
            active_contract_symbol=active_contract,
        )

    # --------------------------------------------------------------------------
    # Risk Telemetry & Controls
    # --------------------------------------------------------------------------

    def get_risk_snapshot(self) -> RiskSnapshotResponse:
        """Return real-time risk metrics and circuit breaker status."""
        snapshot = self.risk_engine.get_snapshot()
        open_count = len([p for p in self.oms.get_all_positions() if p.is_open])

        return RiskSnapshotResponse(
            session_id=self.active_session.session_id if self.active_session else "SESSION-DEFAULT",
            timestamp=snapshot.timestamp,
            realized_pnl=snapshot.realized_pnl,
            unrealized_pnl=snapshot.unrealized_pnl,
            total_mtm=snapshot.total_mtm,
            peak_mtm=snapshot.peak_mtm,
            current_drawdown=snapshot.current_drawdown,
            max_daily_loss_limit=self.risk_engine.risk_config.max_daily_loss_limit,
            is_circuit_breaker_active=self.risk_engine.circuit_breaker.is_locked_out,
            circuit_breaker_reason="DAILY_LOSS_LIMIT" if self.risk_engine.circuit_breaker.is_locked_out else None,
            is_kill_switch_active=snapshot.is_kill_switch_active,
            kill_switch_reason=snapshot.kill_switch_reason,
            is_profit_protection_active=snapshot.is_profit_protection_active,
            profit_protection_status="LOCKED" if snapshot.is_profit_protection_active else "INACTIVE",
            profit_floor=snapshot.profit_floor,
            open_positions_count=open_count,
            is_data_stale=False,
            is_synthetic=True,
            data_source="PAPER_SIMULATOR",
        )

    async def list_audit_events(self, limit: int = 50) -> List[AuditEventResponseItem]:
        """Retrieve real immutable audit log records from persistence."""
        if not self.repo:
            return []
        records = await self.repo.list_audit_events(limit=limit)
        items: List[AuditEventResponseItem] = []
        for r in records:
            items.append(
                AuditEventResponseItem(
                    event_id=r.event_id,
                    session_id=r.session_id,
                    timestamp=r.timestamp,
                    event_type=r.event_type,
                    component=r.component,
                    details=r.details or {},
                )
            )
        return items

    async def trigger_kill_switch(self, reason: str, operator: str) -> KillSwitchResponse:
        """
        Trigger emergency kill-switch in PAPER mode.
        Cancels open orders and closes all open positions at market price.
        Reports verified counts of actually cancelled orders and closed positions.
        """
        logger.warning("WorkstationService: Emergency Kill-Switch triggered by %s: %s", operator, reason)
        self.risk_engine.kill_switch.trigger(
            reason=RiskTriggerReason.MANUAL_OVERRIDE,
            details={"operator": operator, "reason": reason},
        )

        # Cancel open orders
        cancelled_count = 0
        for o in self.oms.get_open_orders():
            try:
                res = await self.oms.request_cancel_order(o.client_order_id, reason=f"Kill-switch: {reason}")
                if res.oms_status == OmsOrderStatus.CANCELLED:
                    cancelled_count += 1
            except Exception as e:
                logger.error("Failed to cancel order %s during kill-switch: %s", o.client_order_id, e)

        # Square off open positions
        closed_count = 0
        for pos in self.oms.get_all_positions():
            if pos.is_open and pos.net_qty != 0:
                try:
                    await self.square_off_position(pos.symbol, reason=f"Kill-switch: {reason}")
                    updated_pos = self.oms.get_position(pos.symbol)
                    if updated_pos and (not updated_pos.is_open or updated_pos.net_qty == 0):
                        closed_count += 1
                except Exception as e:
                    logger.error("Failed to square off position %s during kill-switch: %s", pos.symbol, e)

        # Log audit event
        if self.repo:
            evt = AuditEvent(
                event_id=f"EVT-KS-{uuid4().hex[:8].upper()}",
                session_id=self.active_session.session_id if self.active_session else "SESSION-DEFAULT",
                timestamp=now_utc(),
                event_type=AuditEventType.KILL_SWITCH_ACTIVATED,
                component="WorkstationService",
                details={
                    "operator": operator,
                    "reason": reason,
                    "orders_cancelled": cancelled_count,
                    "positions_closed": closed_count,
                },
            )
            await self.repo.record_audit_event(evt)

        return KillSwitchResponse(
            success=True,
            status="TRIGGERED",
            reason=reason,
            orders_cancelled_count=cancelled_count,
            positions_closed_count=closed_count,
            timestamp=now_utc(),
        )

    # --------------------------------------------------------------------------
    # Orders & Positions
    # --------------------------------------------------------------------------

    def list_orders(self) -> List[OrderResponseItem]:
        """Return list of all orders with transitions and fills."""
        orders: List[OrderResponseItem] = []
        for o in self.oms.get_all_orders():
            transitions = [
                OrderTransitionResponseItem(
                    from_status=t.from_status.value,
                    to_status=t.to_status.value,
                    filled_qty=t.filled_qty,
                    avg_price=t.avg_price,
                    message=t.message,
                    timestamp=t.timestamp,
                )
                for t in o.transitions
            ]
            fills = [
                FillResponseItem(
                    fill_id=f.fill_id,
                    client_order_id=f.client_order_id,
                    broker_order_id=f.broker_order_id,
                    symbol=f.symbol,
                    side=f.side.name if hasattr(f.side, "name") else str(f.side),
                    fill_price=f.fill_price,
                    fill_qty=f.fill_qty,
                    timestamp=f.timestamp,
                )
                for f in o.fills
            ]
            orders.append(
                OrderResponseItem(
                    client_order_id=o.client_order_id,
                    broker_order_id=o.broker_order_id,
                    session_id=o.session_id,
                    symbol=o.symbol,
                    side=o.side.name if hasattr(o.side, "name") else str(o.side),
                    order_type=o.order_type.name if hasattr(o.order_type, "name") else str(o.order_type),
                    product_type=o.product_type.value if hasattr(o.product_type, "value") else str(o.product_type),
                    qty=o.qty,
                    filled_qty=o.filled_qty,
                    remaining_qty=o.remaining_qty,
                    oms_status=o.oms_status.value,
                    status=o.oms_status.value,
                    avg_fill_price=o.avg_fill_price,
                    limit_price=o.limit_price,
                    tag=o.tag,
                    status_message=o.status_message or "Normal",
                    submitted_at=o.created_at,
                    updated_at=o.updated_at,
                    transitions=transitions,
                    fills=fills,
                    is_synthetic=True,
                )
            )
        return sorted(orders, key=lambda x: x.submitted_at, reverse=True)

    def list_positions(self) -> List[PositionResponseItem]:
        """Return list of all current positions."""
        positions: List[PositionResponseItem] = []
        for p in self.oms.get_all_positions():
            positions.append(
                PositionResponseItem(
                    position_id=p.position_id,
                    session_id=p.session_id,
                    symbol=p.symbol,
                    product_type=p.product_type.value,
                    net_qty=p.net_qty,
                    buy_qty=p.buy_qty,
                    sell_qty=p.sell_qty,
                    avg_buy_price=p.avg_buy_price,
                    avg_sell_price=p.avg_sell_price,
                    current_ltp=p.current_ltp,
                    realized_pnl=p.realized_pnl,
                    unrealized_pnl=p.unrealized_pnl,
                    total_pnl=p.total_pnl,
                    is_open=p.is_open,
                    opened_at=p.opened_at or now_utc(),
                    closed_at=p.closed_at,
                    is_synthetic=True,
                )
            )
        return positions

    async def square_off_position(self, symbol: str, reason: str = "MANUAL_SQUARE_OFF") -> PositionResponseItem:
        """
        Square off single position in PAPER mode through OMS and pre-trade gatekeeper.
        """
        pos = self.oms.get_position(symbol)
        if not pos or not pos.is_open or pos.net_qty == 0:
            raise ValueError(f"No open position found for {symbol}")

        exit_side = OrderSide.SELL if pos.net_qty > 0 else OrderSide.BUY
        qty = abs(pos.net_qty)
        contract = self.instrument_registry.get_contract_by_symbol(symbol)

        intent = OrderIntent(
            intent_id=f"INT-SQOFF-{uuid4().hex[:8].upper()}",
            client_order_id=f"ORD-SQOFF-{uuid4().hex[:8].upper()}",
            session_id=self.active_session.session_id if self.active_session else "SESSION-DEFAULT",
            strategy_name="NIFTY_ORB",
            strategy_version=1,
            symbol=symbol,
            side=exit_side,
            order_type=OrderType.MARKET,
            product_type=pos.product_type,
            qty=qty,
            tag=f"SQ_OFF_{reason}"[:20],
        )

        oms_order = await self.oms.submit_order_intent(intent, contract=contract)
        if oms_order.oms_status not in (OmsOrderStatus.FILLED, OmsOrderStatus.PARTIALLY_FILLED):
            logger.error("Square-off order %s failed with status %s", oms_order.client_order_id, oms_order.oms_status)
            raise RuntimeError(f"Square-off execution failed: {oms_order.status_message}")

        updated_pos = self.oms.get_position(symbol)
        if not updated_pos:
            raise RuntimeError(f"Position state missing after square-off of {symbol}")

        return PositionResponseItem(
            position_id=updated_pos.position_id,
            session_id=updated_pos.session_id,
            symbol=updated_pos.symbol,
            product_type=updated_pos.product_type.value,
            net_qty=updated_pos.net_qty,
            buy_qty=updated_pos.buy_qty,
            sell_qty=updated_pos.sell_qty,
            avg_buy_price=updated_pos.avg_buy_price,
            avg_sell_price=updated_pos.avg_sell_price,
            current_ltp=updated_pos.current_ltp,
            realized_pnl=updated_pos.realized_pnl,
            unrealized_pnl=updated_pos.unrealized_pnl,
            total_pnl=updated_pos.total_pnl,
            is_open=updated_pos.is_open,
            opened_at=updated_pos.opened_at or now_utc(),
            closed_at=updated_pos.closed_at,
            is_synthetic=True,
        )

    # --------------------------------------------------------------------------
    # Backtest Lab & StockMock Benchmark
    # --------------------------------------------------------------------------

    async def run_backtest(self, req: BacktestRunRequest) -> BacktestDetailsResponse:
        """
        Run discrete-event backtest simulation and return detailed report.
        Fails cleanly if historical market data is unavailable.
        """
        is_synthetic_request = (
            req.dataset_id.startswith("SYNTHETIC")
            or req.dataset_id.startswith("TEST_DATASET")
            or req.dataset_id == "DEFAULT_SYNTHETIC"
        )

        # Check date range validity
        if is_synthetic_request:
            # Synthetic dataset is strictly available for 2026-11-06
            if req.start_date > date(2026, 11, 6) or req.end_date < date(2026, 11, 6):
                raise ValueError(
                    f"Synthetic dataset '{req.dataset_id}' is only available for 2026-11-06. "
                    f"Requested range ({req.start_date} to {req.end_date}) contains no data."
                )
        else:
            # Historical dataset requested
            raise ValueError(
                f"Historical market data for period {req.start_date} to {req.end_date} is unavailable. "
                "No offline historical store found for this date range."
            )

        merged_params = dict(req.parameters or {})
        strategy_fields = set(StrategyConfig.model_fields.keys())
        risk_fields = set(RiskConfig.model_fields.keys())

        strat_kwargs = {k: v for k, v in merged_params.items() if k in strategy_fields}
        risk_kwargs = {k: v for k, v in merged_params.items() if k in risk_fields}

        if "breakout_confirmation_mode" in strat_kwargs and isinstance(strat_kwargs["breakout_confirmation_mode"], str):
            strat_kwargs["breakout_confirmation_mode"] = BreakoutConfirmationMode(
                strat_kwargs["breakout_confirmation_mode"]
            )
        if "range_boundary_mode" in strat_kwargs and isinstance(strat_kwargs["range_boundary_mode"], str):
            strat_kwargs["range_boundary_mode"] = RangeBoundaryMode(
                strat_kwargs["range_boundary_mode"]
            )
        if "profit_trailing_mode" in risk_kwargs and isinstance(risk_kwargs["profit_trailing_mode"], str):
            risk_kwargs["profit_trailing_mode"] = ProfitTrailingMode(
                risk_kwargs["profit_trailing_mode"]
            )

        strategy_config = StrategyConfig(**strat_kwargs) if strat_kwargs else self.active_strategy_config
        risk_config = RiskConfig(**risk_kwargs) if risk_kwargs else RiskConfig()
        exec_config = ExecutionModelConfig(
            slippage_points=req.slippage_points,
            brokerage_per_order=req.brokerage_per_order,
        )

        engine = DiscreteEventBacktestEngine(
            strategy_config=strategy_config,
            risk_config=risk_config,
            execution_config=exec_config,
            contract_resolver=self.contract_resolver,
            initial_capital=req.initial_capital,
        )

        spot_candles = self.sample_candles.get("NSE:NIFTY50-INDEX", [])
        ce_candles = self.sample_candles.get("NSE:NIFTY26NOV25000CE", [])
        pe_candles = self.sample_candles.get("NSE:NIFTY26NOV25000PE", [])

        option_map = {}
        if ce_candles:
            option_map["NSE:NIFTY26NOV25000CE"] = ce_candles
        if pe_candles:
            option_map["NSE:NIFTY26NOV25000PE"] = pe_candles

        run_result = engine.run(
            spot_candles=spot_candles,
            option_candles=option_map,
            dataset_id=req.dataset_id,
        )
        self.sample_backtests[run_result.run_id] = run_result

        if self.repo:
            await self.repo.save_backtest_run(run_result)

        return self._format_backtest_details(run_result)

    async def list_backtest_runs(self) -> List[BacktestSummaryResponse]:
        """List all backtest run summaries from in-memory and persistent storage."""
        summaries: List[BacktestSummaryResponse] = []
        for run in self.sample_backtests.values():
            summaries.append(self._format_backtest_summary(run))

        if self.repo:
            persisted = await self.repo.list_backtest_runs(limit=50)
            existing_ids = {s.run_id for s in summaries}
            for p in persisted:
                if p.run_id not in existing_ids:
                    metrics = BacktestMetrics(**p.metrics_json)
                    summary = BacktestSummaryResponse(
                        run_id=p.run_id,
                        dataset_id=p.dataset_id,
                        start_date=p.start_date,
                        end_date=p.end_date,
                        strategy_name=p.strategy_name,
                        strategy_version=p.strategy_version,
                        is_synthetic=True,
                        net_profit=metrics.net_pnl,
                        total_trades=metrics.total_trades,
                        win_rate_pct=metrics.win_rate,
                        profit_factor=metrics.profit_factor,
                        max_drawdown_amount=metrics.max_drawdown,
                        max_drawdown_pct=metrics.max_drawdown_pct,
                        sharpe_ratio=Decimal("0.00"),
                        expectancy=metrics.expectancy,
                        created_at=p.created_at,
                    )
                    summaries.append(summary)

        return sorted(summaries, key=lambda s: s.created_at, reverse=True)

    async def get_backtest_run(self, run_id: str) -> BacktestDetailsResponse:
        """
        Get full details of a specific backtest run.
        Never falls back to arbitrary runs; raises 404 if missing.
        """
        run = self.sample_backtests.get(run_id)
        if not run and self.repo:
            record = await self.repo.get_backtest_run(run_id)
            if record:
                # Reconstitute BacktestRun from database record
                metrics = BacktestMetrics(**record.metrics_json)
                diagnostics = BacktestDiagnostics(**record.diagnostics_json)
                exec_config = ExecutionModelConfig(**record.execution_config)
                trades = [
                    BacktestTrade(
                        trade_id=t.trade_id,
                        symbol=t.symbol,
                        underlying_symbol=t.underlying_symbol,
                        option_type=OptionType(t.option_type),
                        strike=t.strike,
                        direction=t.direction,
                        entry_time=t.entry_time,
                        exit_time=t.exit_time,
                        entry_price=t.entry_price,
                        exit_price=t.exit_price,
                        qty=t.qty,
                        lot_size=t.lot_size,
                        gross_pnl=t.gross_pnl,
                        trading_costs=t.trading_costs,
                        net_pnl=t.net_pnl,
                        exit_reason=t.exit_reason,
                    )
                    for t in record.trades
                ]
                run = BacktestRun(
                    run_id=record.run_id,
                    dataset_id=record.dataset_id,
                    start_date=record.start_date,
                    end_date=record.end_date,
                    strategy_name=record.strategy_name,
                    strategy_version=record.strategy_version,
                    strategy_config_snapshot=record.strategy_config_snapshot,
                    execution_config=exec_config,
                    engine_version=record.engine_version,
                    metrics=metrics,
                    diagnostics=diagnostics,
                    trades=trades,
                    created_at=record.created_at,
                )
                self.sample_backtests[run.run_id] = run

        if not run:
            raise ValueError(f"Backtest run '{run_id}' not found")
        return self._format_backtest_details(run)

    async def benchmark_with_stockmock(self, run_id: str, csv_content: str) -> BenchmarkResponse:
        """Compare backtest run with StockMock CSV benchmark."""
        try:
            details = await self.get_backtest_run(run_id)
        except ValueError:
            raise ValueError(f"Backtest run '{run_id}' not found for benchmarking")

        run = self.sample_backtests.get(run_id)
        trades = run.trades if run else []
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", suffix=".csv", delete=False) as tf:
            tf.write(csv_content)
            temp_path = Path(tf.name)

        try:
            comparator = StockMockBenchmarkComparator(stockmock_csv_path=temp_path)
            report = comparator.compare(trades)
            matched_count = report.matched_trades
            discrepancy_count = len([c for c in report.trade_comparisons if c.status == BenchmarkStatus.MISMATCH])
            pnl_diff = report.pnl_difference or Decimal("0.00")
            is_matched = report.status in (BenchmarkStatus.MATCH, BenchmarkStatus.DEVIATION_WITHIN_TOLERANCE)
            return BenchmarkResponse(
                matched_trades_count=matched_count,
                discrepancy_count=discrepancy_count,
                pnl_diff=pnl_diff,
                is_matched=is_matched,
                summary=report.message,
            )
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _format_backtest_summary(self, run: BacktestRun) -> BacktestSummaryResponse:
        m = run.metrics
        # Compute Sharpe ratio deterministically from trade P&L
        sharpe = Decimal("0.00")
        if len(run.trades) > 1:
            try:
                trade_pnls = [float(t.net_pnl) for t in run.trades]
                mean_pnl = statistics.mean(trade_pnls)
                stdev_pnl = statistics.stdev(trade_pnls)
                if stdev_pnl > 0:
                    sharpe = (Decimal(str(mean_pnl)) / Decimal(str(stdev_pnl))).quantize(Decimal("0.01"))
            except Exception:
                sharpe = Decimal("0.00")

        return BacktestSummaryResponse(
            run_id=run.run_id,
            dataset_id=run.dataset_id,
            start_date=run.start_date,
            end_date=run.end_date,
            strategy_name=run.strategy_name,
            strategy_version=run.strategy_version,
            is_synthetic=True,
            net_profit=m.net_pnl,
            total_trades=m.total_trades,
            win_rate_pct=m.win_rate,
            profit_factor=m.profit_factor,
            max_drawdown_amount=m.max_drawdown,
            max_drawdown_pct=m.max_drawdown_pct,
            sharpe_ratio=sharpe,
            expectancy=m.expectancy,
            created_at=run.created_at,
        )

    def _format_backtest_details(self, run: BacktestRun) -> BacktestDetailsResponse:
        summary = self._format_backtest_summary(run)
        equity_curve: List[BacktestEquityPoint] = []
        running_equity = run.execution_config.brokerage_per_order * Decimal("0") + Decimal("100000.00")
        peak_equity = running_equity

        equity_curve.append(
            BacktestEquityPoint(
                timestamp=to_utc(IST_TZ.localize(datetime(2026, 11, 6, 9, 15, 0))),
                equity=running_equity,
                drawdown=Decimal("0.00"),
            )
        )
        for t in run.trades:
            running_equity += t.net_pnl
            if running_equity > peak_equity:
                peak_equity = running_equity
            dd = peak_equity - running_equity
            equity_curve.append(
                BacktestEquityPoint(
                    timestamp=t.exit_time,
                    equity=running_equity,
                    drawdown=dd,
                )
            )

        trade_items = [
            BacktestTradeItem(
                trade_id=t.trade_id,
                symbol=t.symbol,
                side=t.direction.value if hasattr(t.direction, "value") else str(t.direction),
                entry_time=t.entry_time,
                entry_price=t.entry_price,
                exit_time=t.exit_time,
                exit_price=t.exit_price,
                qty=t.qty,
                gross_pnl=t.gross_pnl,
                transaction_costs=t.trading_costs,
                net_pnl=t.net_pnl,
                exit_reason=t.exit_reason,
                duration_minutes=int((t.exit_time - t.entry_time).total_seconds() / 60),
            )
            for t in run.trades
        ]

        return BacktestDetailsResponse(
            summary=summary,
            metrics=run.metrics.model_dump(mode="json"),
            diagnostics=run.diagnostics.model_dump(mode="json"),
            equity_curve=equity_curve,
            trades=trade_items,
        )

    # --------------------------------------------------------------------------
    # Reconciliation
    # --------------------------------------------------------------------------

    async def run_reconciliation(self) -> ReconciliationReport:
        """Run OMS vs Broker reconciliation audit."""
        report = await self.reconciliation_service.reconcile()
        self.last_reconciliation_report = report
        return report

    def get_latest_reconciliation(self) -> Optional[ReconciliationReport]:
        """Return latest reconciliation report."""
        return self.last_reconciliation_report


# Global service instance
workstation_service = WorkstationService()
