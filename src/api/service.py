"""Workstation core service layer.

Bridges the FastAPI endpoints to the underlying engines:
- StateRepository (persistence)
- DiscreteEventBacktestEngine (backtesting)
- OrderManagementSystem & PaperBrokerClient (paper execution)
- RiskEngine & KillSwitchCoordinator (risk management)
- InstrumentMasterRegistry & ContractResolver (data)
"""

import asyncio
from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
import logging
import os
from pathlib import Path
import pandas as pd
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
from src.backtest.metrics import MetricsCalculator
from src.backtest.models import BacktestDiagnostics, BacktestMetrics, BacktestRun, BacktestTrade, ExecutionModelConfig
from src.backtest.synthetic import SyntheticMarketDataBuilder, generate_candle
from src.data.validator import OfflineDatasetValidator
from scripts.run_real_underlying_backtest import UnderlyingPointBacktester
from src.broker.base import BrokerConnectionStatus
from src.broker.models import BrokerOrderBookItem, BrokerPosition
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
    SignalType,
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
from src.broker.fyers_auth import FyersAuthError, FyersAuthManager
from src.broker.fyers import FyersBrokerClient
from src.broker.fyers_order_feed import FyersOrderFeed
from src.data.candle_builder import TickCandleBuilder
from src.data.contract_resolver import ContractResolver
from src.data.fyers_live_feed import FyersLiveFeed
from src.data.live_pipeline import LiveCandlePipeline
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
from src.strategy.orb_nifty import NiftyOrbStrategy

logger = logging.getLogger(__name__)


def _parse_enum(enum_cls: Any, val: Any) -> Any:
    """Helper to parse enum from string name, value, or instance."""
    if isinstance(val, enum_cls):
        return val
    if isinstance(val, str):
        clean = val.strip().lower()
        if enum_cls == BreakoutConfirmationMode and clean in {"wick_touch", "intrabar_tick", "tick_cross"}:
            return BreakoutConfirmationMode.INTRABAR_TICK
        if enum_cls == BreakoutConfirmationMode and clean in {"candle_close", "bar_close"}:
            return BreakoutConfirmationMode.CANDLE_CLOSE
        try:
            return enum_cls(clean)
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
        requested_mode = os.environ.get("EXECUTION_MODE", "paper").strip().lower()
        self.execution_mode = requested_mode if requested_mode in {"paper", "live"} else "paper"
        self._live_execution_requested = (
            self.execution_mode == "live"
            and os.environ.get("LIVE_TRADING_ACK", "").strip().lower() == "true"
            and os.environ.get("LIVE_BROKER_ENABLED", "").strip().lower() == "true"
        )
        self._db_engine = None
        self._session_factory = None
        self.repo: Optional[StateRepository] = None

        # Core Components
        self.instrument_registry = InstrumentMasterRegistry()
        self.contract_resolver = ContractResolver(self.instrument_registry)
        self.paper_broker = PaperBrokerClient()
        self.broker_client = self.paper_broker
        self.order_update_feed: Optional[FyersOrderFeed] = None

        # Active session defaults to current calendar trading date in IST
        today = to_ist(now_utc()).date()
        now_time = to_ist(now_utc()).time()
        initial_phase = (
            SessionPhase.CLOSED
            if (now_time > time(15, 30) or now_time < time(9, 15) or to_ist(now_utc()).weekday() >= 5)
            else SessionPhase.ACTIVE_TRADING
        )
        self.active_session = TradingSession(
            session_id=f"SESSION-{today.strftime('%Y%m%d')}-PAPER",
            trading_date=today,
            execution_mode=ExecutionMode.PAPER,
            phase=initial_phase,
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

        # Strategy and Live Paper Trading state
        self.strategy: Optional[NiftyOrbStrategy] = None
        self.last_candle_time: Optional[datetime] = None
        self.last_tick_time: Optional[datetime] = None
        self.candle_anomaly_stats: Dict[str, int] = {
            "stale": 0,
            "missing": 0,
            "duplicate": 0,
            "out_of_order": 0,
        }
        self._live_flag_manual: bool = False
        self.historical_replay_active: bool = False

        # FYERS authentication and live market-data pipeline (separate from synthetic/replay data)
        self.fyers_auth = FyersAuthManager()
        self.live_pipeline: Optional[LiveCandlePipeline] = None
        self.live_candles: Dict[str, List[Candle]] = {}
        self.live_socket_factory: Optional[Any] = None  # injection point for tests only
        self.live_tick_stale_seconds: float = float(os.environ.get("LIVE_FEED_TICK_STALE_SECONDS", "30"))

        # In-memory caches
        self.active_strategy_config: Optional[StrategyConfig] = None
        self.sample_candles: Dict[str, List[Candle]] = {}
        self.sample_backtests: Dict[str, BacktestRun] = {}
        self.last_reconciliation_report: Optional[ReconciliationReport] = None
        self.is_initialized = False

    def _live_credentials(self) -> Optional[tuple[str, str]]:
        token = self.fyers_auth.get_access_token()
        if not token or not self.fyers_auth.client_id or not self.fyers_auth.is_authenticated:
            return None
        return (self.fyers_auth.client_id, token)

    async def _configure_execution_backend(self) -> None:
        """Select the explicitly authorized live backend; never fall back silently."""
        if self.execution_mode == "live" and not self._live_execution_requested:
            raise RuntimeError(
                "LIVE execution requires LIVE_BROKER_ENABLED=true and LIVE_TRADING_ACK=true."
            )
        if not self._live_execution_requested:
            self.execution_mode = "paper"
            self.broker_client = self.paper_broker
            return

        status = await self.fyers_auth.validate()
        if not status["authenticated"]:
            raise RuntimeError(
                f"LIVE_BROKER_ENABLED requires a validated FYERS session ({status['reason']})."
            )
        client = self.fyers_auth.build_model()
        self.broker_client = FyersBrokerClient(
            client=client,
            config=self.settings.broker,
            execution_mode=ExecutionMode.LIVE,
            app_id=self.fyers_auth.client_id,
            access_token=self.fyers_auth.get_access_token(),
        )
        self.execution_mode = "live"
        self.active_session = self.active_session.model_copy(
            update={
                "session_id": self.active_session.session_id.replace("-PAPER", "-LIVE"),
                "execution_mode": ExecutionMode.LIVE,
            }
        )
        self.risk_engine.session_id = self.active_session.session_id
        self.oms.session = self.active_session
        self.oms.broker_client = self.broker_client
        self.reconciliation_service.broker = self.broker_client

    # --------------------------------------------------------------------------
    # FYERS authentication & live feed state
    # --------------------------------------------------------------------------

    @property
    def live_data_connected(self) -> bool:
        """True only when authenticated AND the WebSocket is open, subscribed and receiving fresh ticks.

        When a live pipeline exists this is derived from actual feed state and cannot be
        assigned. The manual flag is retained only for legacy callers when no pipeline exists.
        """
        pipeline = self.live_pipeline
        if pipeline is not None:
            return bool(pipeline.feed.is_live and self.fyers_auth.is_authenticated)
        return self._live_flag_manual

    @live_data_connected.setter
    def live_data_connected(self, value: bool) -> None:
        self._live_flag_manual = bool(value)

    def _fyers_status_label(self) -> str:
        st = self.fyers_auth.status()
        if st["authenticated"]:
            return "AUTHENTICATED"
        if not st["oauth_configured"]:
            return "NOT_CONFIGURED"
        if not st["token_present"]:
            return "UNAUTHENTICATED"
        return f"AUTH_{st['reason']}" if st["reason"] not in ("NOT_VALIDATED",) else "TOKEN_NOT_VALIDATED"

    def _feed_status_label(self, is_market_open: bool) -> str:
        if self.live_pipeline is not None:
            return self.live_pipeline.feed.connection_state
        return "MARKET_CLOSED" if not is_market_open else "DISCONNECTED"

    def get_live_feed_status(self) -> Dict[str, Any]:
        """Distinct, sanitized stages: authentication, WebSocket, subscription, fresh ticks."""
        auth = self.fyers_auth.status()
        pipeline = self.live_pipeline
        feed = pipeline.snapshot() if pipeline is not None else None
        return {
            "authentication": {
                "authenticated": auth["authenticated"],
                "token_present": auth["token_present"],
                "oauth_configured": auth["oauth_configured"],
                "validated_at": auth["validated_at"],
                "reason": auth["reason"],
            },
            "pipeline_running": pipeline is not None and pipeline.running,
            "websocket_connected": feed["feed"]["websocket_connected"] if feed else False,
            "subscribed": feed["feed"]["subscribed"] if feed else False,
            "fresh_tick_received": feed["feed"]["fresh_tick_received"] if feed else False,
            "state": feed["feed"]["state"] if feed else "NOT_STARTED",
            "live_data_connected": self.live_data_connected,
            "feed": feed,
            "execution_mode": "paper",
        }

    async def fyers_login_url(self) -> Dict[str, Any]:
        return {"login_url": self.fyers_auth.get_login_url(), "redirect_uri": self.fyers_auth.redirect_uri}

    async def fyers_exchange_code(self, auth_code_or_url: str) -> Dict[str, Any]:
        return await self.fyers_auth.exchange_auth_code(auth_code_or_url)

    async def fyers_validate(self) -> Dict[str, Any]:
        result = await self.fyers_auth.validate()
        if not result["authenticated"] and self.live_pipeline is not None:
            await self.stop_live_feed()
        return result

    async def fyers_logout(self) -> Dict[str, Any]:
        await self.stop_live_feed()
        self.fyers_auth.clear_token()
        self.fyers_auth._set_state(False, "LOGGED_OUT")
        return self.fyers_auth.status()

    async def start_live_feed(self) -> Dict[str, Any]:
        """Start the real FYERS WebSocket pipeline. Requires a *validated* FYERS session."""
        if self.live_pipeline is not None:
            return self.get_live_feed_status()
        status = await self.fyers_auth.validate()
        if not status["authenticated"]:
            raise FyersAuthError(
                f"FYERS is not authenticated ({status['reason']}); live feed not started. No data is substituted."
            )
        symbol = (self.active_strategy_config or StrategyConfig()).underlying_symbol

        # Live data starts from a clean, separate state (never merged with synthetic/replay).
        self.historical_replay_active = False
        self.last_candle_time = None
        self.live_candles = {}
        self.strategy = None
        if not self.active_session.is_active:
            self.start_session()

        feed = FyersLiveFeed(
            symbol=symbol,
            credentials_provider=self._live_credentials,
            socket_factory=self.live_socket_factory,
            stale_after_seconds=self.live_tick_stale_seconds,
        )
        builder = TickCandleBuilder(symbol=symbol)
        pipeline = LiveCandlePipeline(feed, builder, on_candle=self._on_live_candle, on_event=self._on_live_event)
        await pipeline.start()
        self.live_pipeline = pipeline
        if self.execution_mode == "live" and self.order_update_feed is None:
            from src.websocket.order_updates import OrderUpdateWebSocketService

            order_updates = OrderUpdateWebSocketService(self.oms)
            loop = asyncio.get_running_loop()

            def dispatch_order_frame(frame: Dict[str, Any]) -> None:
                asyncio.run_coroutine_threadsafe(
                    order_updates.process_incoming_frame(frame),
                    loop,
                )

            self.order_update_feed = FyersOrderFeed(
                credentials_provider=self._live_credentials,
                on_frame=dispatch_order_frame,
            )
            await self.order_update_feed.start()
        return self.get_live_feed_status()

    async def stop_live_feed(self) -> Dict[str, Any]:
        pipeline, self.live_pipeline = self.live_pipeline, None
        if pipeline is not None:
            await pipeline.stop()
        if self.order_update_feed is not None:
            await self.order_update_feed.stop()
            self.order_update_feed = None
        return self.get_live_feed_status()

    async def _on_live_candle(self, candle: Candle) -> None:
        await self.process_live_candle(candle, source="LIVE")

    def _on_live_event(self, kind: str, reason: str) -> None:
        if kind == "auth_failed":
            self.fyers_auth.invalidate("FEED_AUTH_REJECTED")

    async def initialize(self) -> None:
        """Initialize database, repository, restore persisted state or seed default templates."""
        if self.is_initialized:
            return

        db_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./workstation_paper.db")
        self._db_engine, self._session_factory = create_engine_and_factory(
            database_url=db_url,
            echo=False,
        )
        await init_db(self._db_engine)
        self._db_session = self._session_factory()
        self.repo = StateRepository(self._db_session)
        self.oms.state_repo = self.repo

        await self._configure_execution_backend()

        # Connect the selected broker backend. Live mode remains fail-closed if
        # authentication or the explicit authorization flags are absent.
        self.paper_broker.set_connection_status(BrokerConnectionStatus.CONNECTED)
        if self.execution_mode == "live":
            auth_status = await self.broker_client.check_auth()
            if not auth_status.is_authenticated:
                raise RuntimeError("Validated FYERS session could not be established for live execution.")
            self.last_reconciliation_report = await self.reconciliation_service.reconcile()
            if not self.last_reconciliation_report.is_synchronized:
                raise RuntimeError(
                    "Live startup blocked: broker and local state are not reconciled."
                )

        # 1. Parse Instrument Master
        instrument_source = os.environ.get("FYERS_INSTRUMENT_MASTER_PATH")
        if self.execution_mode == "live":
            instrument_cache = instrument_source or os.environ.get(
                "FYERS_INSTRUMENT_MASTER_CACHE_PATH", "./secrets/NSE_FO.csv"
            )
            self.instrument_registry = InstrumentMasterRegistry.load_official(
                cache_path=instrument_cache,
                url=os.environ.get(
                    "FYERS_INSTRUMENT_MASTER_URL",
                    "https://public.fyers.in/sym_details/NSE_FO.csv",
                ),
            )
            self.contract_resolver = ContractResolver(self.instrument_registry)
        elif instrument_source:
            self.instrument_registry.parse_csv(Path(instrument_source))
        else:
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

        # 3. Strategy Configuration: Load existing active from DB or seed default template
        latest_active = await self.repo.get_latest_active_config("NSE:NIFTY50-INDEX") if self.repo else None
        if latest_active:
            snapshot = self._record_to_snapshot(latest_active)
            self.apply_strategy_config(snapshot)
            logger.info("Restored persisted active strategy config %s (v%d)", snapshot.config_id, snapshot.version)
        else:
            default_snapshot = StrategyConfigSnapshot(
                config_id="NIFTY_ORB_V1",
                version=1,
                underlying_symbol="NSE:NIFTY50-INDEX",
                strategy_name="NIFTY ORB Production Strategy",
                orb_start_time=time(9, 16, 0),
                orb_end_time=time(9, 45, 0),
                mandatory_exit_time=time(15, 10, 0),
                lots=1,
                lot_size=25,
                trading_days=["Monday", "Thursday", "Friday"],
                breakout_confirmation_mode="candle_close",
                range_boundary_mode="up_to_0944_close",
                order_execution_style="market",
                expiry_day_selection="current_weekly_0dte",
                strike_selection_mode="ATM",
                entry_start_time=time(9, 45, 0),
                entry_end_time=time(15, 15, 0),
                max_daily_loss_limit=Decimal("3000.00"),
                profit_protection_trigger=Decimal("3000.00"),
                profit_protection_min_lock=Decimal("500.00"),
                profit_trailing_mode="lock_and_trail",
                profit_step=Decimal("1000.00"),
                profit_lock_increment=Decimal("500.00"),
                parameters={
                    "strategy_name": "NIFTY ORB Production Strategy",
                    "underlying_symbol": "NSE:NIFTY50-INDEX",
                    "orb_start_time": "09:16:00",
                    "orb_end_time": "09:45:00",
                    "mandatory_exit_time": "15:10:00",
                    "lots": 1,
                    "lot_size": 25,
                    "trading_days": ["Monday", "Thursday", "Friday"],
                    "breakout_confirmation_mode": "candle_close",
                    "range_boundary_mode": "up_to_0944_close",
                    "order_execution_style": "market",
                    "expiry_day_selection": "current_weekly_0dte",
                    "strike_selection_mode": "ATM",
                    "max_daily_loss": 3000.0,
                    "profit_protection_trigger": 3000.0,
                    "profit_protection_min_lock": 500.0,
                    "profit_trailing_mode": "lock_and_trail",
                    "profit_step": 1000.0,
                    "profit_lock_increment": 500.0,
                },
                created_at=now_utc(),
            )
            if self.repo:
                await self.repo.save_strategy_config(default_snapshot, is_active=True)
            self.apply_strategy_config(default_snapshot)

        # 4. Opening Range for session (Clean unestablished state by default)
        today = self.active_session.trading_date or to_ist(now_utc()).date()
        self.active_opening_range = OpeningRange(
            symbol=self.active_strategy_config.underlying_symbol,
            session_date=today,
            start_time=self.active_strategy_config.orb_start_time,
            end_time=self.active_strategy_config.orb_end_time,
            high=None,
            low=None,
            is_established=False,
            candle_count=0,
        )

        # 5. Reload persisted Backtest Runs from database for restart recovery
        persisted_runs = await self.repo.list_backtest_runs(limit=20)
        if persisted_runs:
            for pr in persisted_runs:
                # Store into in-memory cache
                pass

        # Generate baseline synthetic backtest run if none exists
        engine = DiscreteEventBacktestEngine(
            strategy_config=self.active_strategy_config,
            risk_config=self.risk_config,
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

        # 6. Seed sample paper trading position only if explicitly enabled via environment variable
        enable_demo = os.environ.get("ENABLE_DEMO_FIXTURES", "").strip().lower() in {"1", "true", "yes"}
        if enable_demo:
            await self._seed_demo_order_and_position()

        self.is_initialized = True

        # If a daily token is already stored, verify it with a real FYERS request (non-blocking).
        if self.fyers_auth.status()["token_present"]:
            self._auth_validation_task = asyncio.create_task(self.fyers_auth.validate())
        logger.info("WorkstationService initialized in %s mode (DB-connected).", self.execution_mode.upper())

    async def _seed_demo_order_and_position(self) -> None:
        """Seed a demonstration order and position explicitly tagged as DEMO_FIXTURE."""
        today = self.active_session.trading_date or to_ist(now_utc()).date()
        date_str = today.strftime("%Y%m%d")
        rand_id = uuid4().hex[:6].upper()
        client_oid = f"ORD-{date_str}-{rand_id}"
        broker_oid = f"PB-ORD-DEMO-{rand_id}"
        demo_time = to_utc(IST_TZ.localize(datetime.combine(today, time(9, 46, 0))))
        demo_time_fill = to_utc(IST_TZ.localize(datetime.combine(today, time(9, 46, 1))))

        oms_order = OmsOrder(
            client_order_id=client_oid,
            session_id=self.active_session.session_id,
            intent_id=f"DEMO-INT-{date_str}-{rand_id}",
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
            broker_order_id=broker_oid,
            avg_fill_price=Decimal("120.00"),
            status_message="Demo fill at 120.00",
            created_at=demo_time,
            updated_at=demo_time_fill,
        )
        oms_order.transitions.append(
            OmsOrderStateTransition(
                client_order_id=client_oid,
                from_status=OmsOrderStatus.SUBMISSION_PENDING,
                to_status=OmsOrderStatus.OPEN,
                filled_qty=0,
                avg_price=Decimal("0.00"),
                message="Accepted in paper simulator",
                timestamp=demo_time,
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
                timestamp=demo_time_fill,
            )
        )
        fill = Fill(
            fill_id=f"DEMO-FILL-{rand_id}",
            client_order_id=client_oid,
            broker_order_id=broker_oid,
            symbol="NSE:NIFTY26NOV25000CE",
            side=OrderSide.BUY,
            fill_price=Decimal("120.00"),
            fill_qty=25,
            timestamp=demo_time_fill,
        )
        oms_order.fills.append(fill)
        self.oms._orders[client_oid] = oms_order
        self.oms._broker_to_client[broker_oid] = client_oid

        # Seed broker order book with matching filled order
        self.paper_broker._orders[broker_oid] = BrokerOrderBookItem(
            broker_order_id=broker_oid,
            client_order_id=client_oid,
            symbol="NSE:NIFTY26NOV25000CE",
            qty=25,
            filled_qty=25,
            remaining_qty=0,
            status=OrderStatus.FILLED,
            order_type=OrderType.MARKET,
            side=OrderSide.BUY,
            avg_price=Decimal("120.00"),
            limit_price=Decimal("0.00"),
            order_timestamp=demo_time,
            message="Filled in paper simulation",
            tag="DEMO_FIXTURE",
        )

        pos = Position(
            position_id=f"DEMO-POS-{date_str}-{rand_id}",
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
            opened_at=demo_time_fill,
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
                event_id=f"EVT-SEED-{rand_id}",
                session_id=self.active_session.session_id,
                timestamp=now_utc(),
                event_type=AuditEventType.SESSION_BOOTSTRAP,
                component="WorkstationService",
                details={"mode": "PAPER", "status": "INITIALIZED"},
            )
            await self.repo.record_audit_event(evt)

    async def seed_demo_orders(self) -> Dict[str, Any]:
        """Explicitly seed demo order and position fixtures for testing/demonstration."""
        await self._seed_demo_order_and_position()
        return {
            "status": "SEEDED",
            "message": "Demo fixtures seeded successfully (1 order, 1 position tagged DEMO_FIXTURE).",
            "orders_count": len(self.oms.get_all_orders()),
            "positions_count": len([p for p in self.oms.get_all_positions() if p.is_open]),
        }

    async def clear_orders_and_positions(self) -> Dict[str, Any]:
        """Clear all active and historical orders, positions, and MTM tracker state."""
        self.oms._orders.clear()
        self.oms._broker_to_client.clear()
        self.oms._positions.clear()
        self.paper_broker._orders.clear()
        self.paper_broker._positions.clear()
        self.risk_engine.tracker.reset()
        if self.repo:
            report = await self.reconciliation_service.reconcile()
            self.last_reconciliation_report = report
        return {
            "status": "CLEARED",
            "message": "All orders, positions, and MTM tracking state cleared successfully.",
            "orders_count": 0,
            "positions_count": 0,
        }

    # --------------------------------------------------------------------------
    # Health & System Telemetry
    # --------------------------------------------------------------------------

    async def get_system_health(self) -> SystemHealthResponse:
        """Return authentic system health, broker connectivity, and storage status."""
        now_ist = datetime.now(IST_TZ)
        is_weekday = now_ist.weekday() < 5
        market_open = time(9, 15, 0)
        market_close = time(15, 30, 0)
        is_market_open = is_weekday and (market_open <= now_ist.time() <= market_close)

        # Authenticated == a real FYERS API request succeeded (never mere token presence).
        app_id = self.fyers_auth.client_id
        fyers_authenticated = self.fyers_auth.is_authenticated
        fyers_status = self._fyers_status_label()

        feed_connected = self.live_data_connected and fyers_authenticated
        feed_status = self._feed_status_label(is_market_open)
        data_source = "LIVE" if feed_connected else ("HISTORICAL" if self.historical_replay_active else "SYNTHETIC")

        return SystemHealthResponse(
            status="HEALTHY",
            execution_mode=self.execution_mode,
            timestamp=now_utc(),
            broker_connected=self.broker_client.is_connected,
            broker_status="CONNECTED" if self.broker_client.is_connected else "DISCONNECTED",
            fyers_authenticated=fyers_authenticated,
            fyers_status=fyers_status,
            market_data_feed_connected=feed_connected,
            market_data_feed_status=feed_status,
            market_data_healthy=feed_connected or self.live_pipeline is None,
            market_data_stale=(self.live_pipeline is not None and not self.live_pipeline.feed.tick_fresh),
            data_source=data_source,
            is_market_open=is_market_open,
            websocket_state=self.live_pipeline.feed.connection_state if self.live_pipeline else "NOT_STARTED",
            live_feed_subscribed=bool(self.live_pipeline and self.live_pipeline.feed.subscribed),
            live_feed_fresh_tick=bool(self.live_pipeline and self.live_pipeline.feed.tick_fresh),
            fyers_auth_reason=self.fyers_auth.status()["reason"],
            live_last_ltp=(str(self.live_pipeline.feed.last_ltp) if (self.live_pipeline and self.live_pipeline.feed.is_live and self.live_pipeline.feed.last_ltp is not None) else None),
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

            if "opening_range_start" in merged_params and "orb_start_time" not in strat_kwargs:
                val = merged_params["opening_range_start"]
                if isinstance(val, str) and ":" in val:
                    parts = val.split(":")
                    strat_kwargs["orb_start_time"] = time(int(parts[0]), int(parts[1]))
            if "opening_range_end" in merged_params and "orb_end_time" not in strat_kwargs:
                val = merged_params["opening_range_end"]
                if isinstance(val, str) and ":" in val:
                    parts = val.split(":")
                    strat_kwargs["orb_end_time"] = time(int(parts[0]), int(parts[1]))

            if "breakout_confirmation" in merged_params and "breakout_confirmation_mode" not in strat_kwargs:
                strat_kwargs["breakout_confirmation_mode"] = _parse_enum(
                    BreakoutConfirmationMode, merged_params["breakout_confirmation"]
                )
            elif "breakout_confirmation_mode" in strat_kwargs:
                strat_kwargs["breakout_confirmation_mode"] = _parse_enum(
                    BreakoutConfirmationMode, strat_kwargs["breakout_confirmation_mode"]
                )

            if "range_boundary_mode" in strat_kwargs:
                strat_kwargs["range_boundary_mode"] = _parse_enum(
                    RangeBoundaryMode, strat_kwargs["range_boundary_mode"]
                )

            if "trading_days" in merged_params:
                td = merged_params["trading_days"]
                if isinstance(td, list):
                    strat_kwargs["trading_days"] = [str(d).strip().capitalize() for d in td]

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

    def _record_to_snapshot(self, record: Any) -> StrategyConfigSnapshot:
        """Convert a StrategyConfigRecord from the database into a StrategyConfigSnapshot."""
        data = dict(record.config_json or {})
        data["config_id"] = record.config_id
        data["version"] = record.version
        data["underlying_symbol"] = record.underlying_symbol
        data["created_at"] = record.created_at
        return StrategyConfigSnapshot.model_validate(data)

    def _build_snapshot_from_dict(
        self,
        config_id: str,
        version: int,
        underlying_symbol: str,
        params: Dict[str, Any],
    ) -> StrategyConfigSnapshot:
        """Build and validate a StrategyConfigSnapshot from a parameter dictionary."""
        def parse_time(val: Any, default: time) -> time:
            if val is None:
                return default
            if isinstance(val, time):
                return val
            if isinstance(val, str):
                parts = val.strip().split(":")
                return time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
            return default

        orb_start = parse_time(
            params.get("orb_start_time") or params.get("opening_range_start"),
            time(9, 16, 0),
        )
        orb_end = parse_time(
            params.get("orb_end_time") or params.get("opening_range_end"),
            time(9, 45, 0),
        )
        mandatory_exit = parse_time(
            params.get("mandatory_exit_time") or params.get("square_off_time"),
            time(15, 10, 0),
        )
        entry_start = parse_time(
            params.get("entry_start_time"),
            time(9, 45, 0),
        )
        entry_end = parse_time(
            params.get("entry_end_time"),
            time(15, 15, 0),
        )

        if orb_end <= orb_start:
            raise ValueError(f"orb_end_time ({orb_end}) must be strictly after orb_start_time ({orb_start})")
        if mandatory_exit <= orb_end:
            raise ValueError(f"mandatory_exit_time ({mandatory_exit}) must be strictly after orb_end_time ({orb_end})")

        lots = int(params.get("lots", 1))
        if lots < 1:
            raise ValueError("Lots must be >= 1")
        lot_size = int(params.get("lot_size", 25))

        raw_days = params.get("trading_days", ["Monday", "Thursday", "Friday"])
        if isinstance(raw_days, str):
            trading_days = [d.strip().capitalize() for d in raw_days.split(",") if d.strip()]
        else:
            trading_days = [str(d).strip().capitalize() for d in raw_days]

        breakout_mode = str(
            params.get("breakout_confirmation_mode") or params.get("breakout_confirmation", "candle_close")
        ).lower()
        range_boundary = str(params.get("range_boundary_mode", "up_to_0944_close")).lower()
        order_style = str(params.get("order_execution_style", "market")).lower()
        expiry_mode = str(params.get("expiry_day_selection", "current_weekly_0dte")).lower()
        strike_mode = str(params.get("strike_selection_mode", "ATM")).upper()

        max_loss = Decimal(str(params.get("max_daily_loss", params.get("max_loss_limit", 3000.0))))
        profit_trigger = Decimal(
            str(params.get("profit_protection_trigger", params.get("profit_protection_threshold", 3000.0)))
        )
        min_lock = Decimal(
            str(params.get("profit_protection_min_lock", params.get("minimum_profit_lock", params.get("profit_floor_initial", 500.0))))
        )
        profit_trailing = str(params.get("profit_trailing_mode", "lock_and_trail")).lower()
        profit_step = Decimal(
            str(params.get("profit_step", params.get("profit_trailing_step_profit", 1000.0)))
        )
        profit_lock_inc = Decimal(
            str(params.get("profit_lock_increment", params.get("profit_trailing_step_lock", 500.0)))
        )

        if min_lock >= profit_trigger:
            raise ValueError(
                f"profit_protection_min_lock ({min_lock}) must be strictly less than profit_trigger ({profit_trigger})"
            )

        merged_params = dict(params)
        merged_params.update({
            "strategy_name": str(params.get("strategy_name", "NIFTY ORB Production Strategy")),
            "underlying_symbol": underlying_symbol,
            "lots": lots,
            "lot_size": lot_size,
            "orb_start_time": orb_start.strftime("%H:%M:%S"),
            "orb_end_time": orb_end.strftime("%H:%M:%S"),
            "mandatory_exit_time": mandatory_exit.strftime("%H:%M:%S"),
            "trading_days": trading_days,
            "breakout_confirmation_mode": breakout_mode,
            "range_boundary_mode": range_boundary,
            "order_execution_style": order_style,
            "expiry_day_selection": expiry_mode,
            "strike_selection_mode": strike_mode,
            "max_daily_loss": float(max_loss),
            "profit_protection_trigger": float(profit_trigger),
            "profit_protection_min_lock": float(min_lock),
            "profit_trailing_mode": profit_trailing,
            "profit_step": float(profit_step),
            "profit_lock_increment": float(profit_lock_inc),
        })

        return StrategyConfigSnapshot(
            config_id=config_id,
            version=version,
            underlying_symbol=underlying_symbol,
            strategy_name=str(params.get("strategy_name", "NIFTY ORB Production Strategy")),
            orb_start_time=orb_start,
            orb_end_time=orb_end,
            mandatory_exit_time=mandatory_exit,
            lots=lots,
            lot_size=lot_size,
            trading_days=trading_days,
            breakout_confirmation_mode=breakout_mode,
            range_boundary_mode=range_boundary,
            order_execution_style=order_style,
            expiry_day_selection=expiry_mode,
            strike_selection_mode=strike_mode,
            entry_start_time=entry_start,
            entry_end_time=entry_end,
            max_daily_loss_limit=max_loss,
            profit_protection_trigger=profit_trigger,
            profit_protection_min_lock=min_lock,
            profit_trailing_mode=profit_trailing,
            profit_step=profit_step,
            profit_lock_increment=profit_lock_inc,
            parameters=merged_params,
            created_at=now_utc(),
        )

    def _snapshot_to_strategy_config(self, snapshot: StrategyConfigSnapshot) -> StrategyConfig:
        return StrategyConfig(
            underlying_symbol=snapshot.underlying_symbol,
            orb_start_time=snapshot.orb_start_time,
            orb_end_time=snapshot.orb_end_time,
            mandatory_exit_time=snapshot.mandatory_exit_time,
            lots=snapshot.lots,
            lot_size=snapshot.lot_size,
            trading_days=snapshot.trading_days,
            breakout_confirmation_mode=BreakoutConfirmationMode(snapshot.breakout_confirmation_mode)
            if isinstance(snapshot.breakout_confirmation_mode, str)
            else snapshot.breakout_confirmation_mode,
            range_boundary_mode=RangeBoundaryMode(snapshot.range_boundary_mode)
            if isinstance(snapshot.range_boundary_mode, str)
            else snapshot.range_boundary_mode,
            order_execution_style=OrderExecutionStyle(snapshot.order_execution_style)
            if isinstance(snapshot.order_execution_style, str)
            else snapshot.order_execution_style,
            expiry_day_selection=ExpiryDaySelectionMode(snapshot.expiry_day_selection)
            if isinstance(snapshot.expiry_day_selection, str)
            else snapshot.expiry_day_selection,
        )

    def _snapshot_to_risk_config(self, snapshot: StrategyConfigSnapshot) -> RiskConfig:
        trailing_mode = None
        if snapshot.profit_trailing_mode:
            try:
                trailing_mode = ProfitTrailingMode(snapshot.profit_trailing_mode)
            except ValueError:
                trailing_mode = ProfitTrailingMode.LOCK_AND_TRAIL
        return RiskConfig(
            max_daily_loss_limit=snapshot.max_daily_loss_limit,
            profit_protection_trigger=snapshot.profit_protection_trigger,
            profit_protection_min_lock=snapshot.profit_protection_min_lock,
            profit_trailing_mode=trailing_mode,
            profit_trailing_step_profit=snapshot.profit_step,
            profit_trailing_step_lock=snapshot.profit_lock_increment,
        )

    def apply_strategy_config(self, snapshot: StrategyConfigSnapshot) -> None:
        """Apply active strategy and risk configurations dynamically to all runtime components."""
        self.active_strategy_config = self._snapshot_to_strategy_config(snapshot)
        self.risk_config = self._snapshot_to_risk_config(snapshot)
        self.risk_engine.risk_config = self.risk_config
        self.risk_engine.circuit_breaker.max_daily_loss = self.risk_config.max_daily_loss_limit
        self.risk_engine.profit_protection.trigger_profit = self.risk_config.profit_protection_trigger
        self.risk_engine.profit_protection.min_lock = self.risk_config.profit_protection_min_lock
        self.risk_engine.profit_protection.step_profit = self.risk_config.profit_trailing_step_profit
        self.risk_engine.profit_protection.step_lock = self.risk_config.profit_trailing_step_lock
        self.risk_engine.profit_protection.mode = (
            self.risk_config.profit_trailing_mode or ProfitTrailingMode.LOCK_MINIMUM_ONLY
        )
        self.risk_engine.gatekeeper.allowed_trading_days = set(self.active_strategy_config.trading_days)
        self.risk_engine.gatekeeper.orb_start_time = self.active_strategy_config.orb_start_time
        self.risk_engine.gatekeeper.mandatory_exit_time = self.active_strategy_config.mandatory_exit_time
        self.strategy = NiftyOrbStrategy(
            strategy_config=self.active_strategy_config,
            risk_config=self.risk_config,
            contract_resolver=self.contract_resolver,
        )
        if self.active_session:
            self.strategy.on_session_start(self.active_session.session_id, self.active_session.trading_date)

    async def create_strategy_config(
        self, req: StrategyConfigCreateRequest
    ) -> StrategyConfigVersionResponse:
        """Create a new strategy configuration record."""
        config_id = req.config_id or f"CONFIG-{uuid4().hex[:8].upper()}"
        existing = await self.repo.list_strategy_configs() if self.repo else []
        underlying_configs = [m for m in existing if m.underlying_symbol == req.underlying_symbol]
        version = max([m.version for m in underlying_configs], default=0) + 1

        snapshot = self._build_snapshot_from_dict(
            config_id=config_id,
            version=version,
            underlying_symbol=req.underlying_symbol,
            params=req.parameters,
        )
        if self.repo:
            await self.repo.save_strategy_config(snapshot, is_active=True)
        self.apply_strategy_config(snapshot)
        return StrategyConfigVersionResponse(
            config_id=config_id,
            version=version,
            underlying_symbol=req.underlying_symbol,
            parameters=snapshot.parameters,
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

        snapshot = self._build_snapshot_from_dict(
            config_id=f"{config_id}_v{new_version}",
            version=new_version,
            underlying_symbol=underlying,
            params=req.parameters,
        )
        if self.repo:
            await self.repo.save_strategy_config(snapshot, is_active=req.set_active)
        if req.set_active:
            self.apply_strategy_config(snapshot)
        return StrategyConfigVersionResponse(
            config_id=config_id,
            version=new_version,
            underlying_symbol=underlying,
            parameters=snapshot.parameters,
            is_active=req.set_active,
            created_at=snapshot.created_at,
        )

    async def activate_strategy_config(self, config_id: str) -> StrategyConfigVersionResponse:
        """Activate a specific configuration version and apply it immediately to the runtime."""
        if not self.repo:
            raise ValueError("Database repository unavailable")
        configs = await self.repo.list_strategy_configs()
        target = next((c for c in configs if c.config_id == config_id), None)
        if not target:
            raise ValueError(f"Strategy configuration '{config_id}' not found")

        await self.repo.set_active_config(config_id, target.underlying_symbol)
        active_rec = await self.repo.get_latest_active_config(target.underlying_symbol)
        if not active_rec:
            raise ValueError(f"Failed to retrieve activated record for '{config_id}'")
        snapshot = self._record_to_snapshot(active_rec)
        self.apply_strategy_config(snapshot)

        return StrategyConfigVersionResponse(
            config_id=snapshot.config_id,
            version=snapshot.version,
            underlying_symbol=snapshot.underlying_symbol,
            parameters=snapshot.parameters,
            is_active=True,
            created_at=snapshot.created_at,
        )

    async def get_active_strategy_config(self) -> StrategyConfigVersionResponse:
        """Return currently active strategy configuration from database or runtime."""
        if self.repo:
            active_rec = await self.repo.get_latest_active_config("NSE:NIFTY50-INDEX")
            if active_rec:
                snapshot = self._record_to_snapshot(active_rec)
                return StrategyConfigVersionResponse(
                    config_id=snapshot.config_id,
                    version=snapshot.version,
                    underlying_symbol=snapshot.underlying_symbol,
                    parameters=snapshot.parameters,
                    is_active=True,
                    created_at=snapshot.created_at,
                )
        params = self.active_strategy_config.model_dump(mode="json") if self.active_strategy_config else {}
        return StrategyConfigVersionResponse(
            config_id="NIFTY_ORB_ACTIVE",
            version=1,
            underlying_symbol="NSE:NIFTY50-INDEX",
            parameters=params,
            is_active=True,
            created_at=now_utc(),
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
        if self.live_pipeline is not None:
            # Live mode: serve ONLY candles built from genuine FYERS ticks (never synthetic).
            candles = self.live_candles.get(symbol, [])
            is_synthetic = False
            provenance = "LIVE_FYERS_TICK_AGGREGATED" if candles else "UNAVAILABLE"
            data_source = "LIVE" if candles else "UNAVAILABLE"
        else:
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
        start_t = (
            self.active_strategy_config.orb_start_time.strftime("%H:%M:%S")
            if self.active_strategy_config
            else "09:16:00"
        )
        end_t = (
            self.active_strategy_config.orb_end_time.strftime("%H:%M:%S")
            if self.active_strategy_config
            else "09:45:00"
        )
        if self.strategy and self.strategy.range_tracker.is_established:
            orb = self.strategy.range_tracker.to_domain_model()
            orb_resp = OpeningRangeResponse(
                is_established=orb.is_established,
                high=orb.high,
                low=orb.low,
                range_width=(orb.high - orb.low) if orb.high and orb.low else None,
                start_time=start_t,
                end_time=end_t,
                candle_count=orb.candle_count,
            )
        elif self.active_opening_range:
            orb = self.active_opening_range
            orb_resp = OpeningRangeResponse(
                is_established=orb.is_established,
                high=orb.high,
                low=orb.low,
                range_width=(orb.high - orb.low) if orb.high and orb.low else None,
                start_time=start_t,
                end_time=end_t,
                candle_count=orb.candle_count,
            )

        active_contract = None
        for p in self.oms.get_all_positions():
            if p.is_open and p.net_qty != 0:
                active_contract = p.symbol
                break

        strat_state = "IDLE"
        if self.strategy:
            strat_state = self.strategy.state.value
        elif active_contract:
            strat_state = "POSITION_OPEN"
        else:
            strat_state = "WAITING_FOR_ORB"

        data_source = "LIVE" if self.live_data_connected else ("HISTORICAL" if self.historical_replay_active else "SYNTHETIC")
        is_synthetic = not self.live_data_connected and not self.historical_replay_active

        return SessionActiveResponse(
            session_id=self.active_session.session_id,
            trading_date=self.active_session.trading_date,
            execution_mode=self.execution_mode,
            phase=self.active_session.phase.value,
            is_active=self.active_session.is_active,
            started_at=self.active_session.started_at,
            ended_at=self.active_session.ended_at,
            strategy_state=strat_state,
            opening_range=orb_resp,
            active_contract_symbol=active_contract,
            data_source=data_source,
            is_synthetic=is_synthetic,
        )

    def start_session(self, trading_date: Optional[date] = None) -> SessionActiveResponse:
        """Start or restart the paper trading session."""
        sess_date = trading_date or to_ist(now_utc()).date()
        sess_id = f"SESSION-{sess_date.strftime('%Y%m%d')}-{self.execution_mode.upper()}"
        self.active_session = self.active_session.model_copy(
            update={
                "session_id": sess_id,
                "trading_date": sess_date,
                "phase": SessionPhase.ACTIVE_TRADING,
                "is_active": True,
                "started_at": now_utc(),
                "ended_at": None,
            }
        )

        if self.strategy:
            self.strategy.on_session_start(self.active_session.session_id, sess_date)
            if self.strategy.range_tracker.is_established:
                self.active_opening_range = self.strategy.range_tracker.to_domain_model()

        return self.get_active_session()

    def stop_session(self) -> SessionActiveResponse:
        """Gracefully stop active trading session."""
        self.active_session = self.active_session.model_copy(
            update={
                "phase": SessionPhase.POST_MARKET,
                "is_active": False,
                "ended_at": now_utc(),
            }
        )
        return self.get_active_session()

    def get_market_data_status(self) -> Dict[str, Any]:
        """Return dynamic market data feed and WebSocket connectivity health."""
        now_ist = datetime.now(IST_TZ)
        is_weekday = now_ist.weekday() < 5
        market_open = time(9, 15, 0)
        market_close = time(15, 30, 0)
        is_market_open = is_weekday and (market_open <= now_ist.time() <= market_close)

        # Authenticated == a real FYERS API request succeeded (never mere token presence).
        app_id = self.fyers_auth.client_id
        fyers_authenticated = self.fyers_auth.is_authenticated
        fyers_status = self._fyers_status_label()

        is_stale = False
        if self.last_candle_time:
            age = (now_utc() - to_utc(self.last_candle_time)).total_seconds()
            if is_market_open and age > 300:
                is_stale = True

        feed_connected = self.live_data_connected and fyers_authenticated
        feed_status = self._feed_status_label(is_market_open)
        data_source = "LIVE" if feed_connected else ("HISTORICAL" if self.historical_replay_active else "SYNTHETIC")

        status = "HEALTHY" if (feed_connected or not is_stale) else ("CLOSED" if not is_market_open else "UNAUTHENTICATED")

        return {
            "status": status,
            "backend_status": "HEALTHY",
            "fyers_authenticated": fyers_authenticated,
            "fyers_status": fyers_status,
            "broker_status": "CONNECTED" if self.broker_client.is_connected else "DISCONNECTED",
            "feed_connected": feed_connected,
            "feed_status": feed_status,
            "data_source": data_source,
            "is_synthetic": data_source == "SYNTHETIC",
            "is_stale": is_stale,
            "is_market_open": is_market_open,
            "current_time_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
            "market_operating_window": "09:15:00 to 15:30:00 IST",
            "has_credentials": bool(app_id),
            "reconnect_count": self.live_pipeline.feed.reconnect_count if self.live_pipeline else 0,
            "last_candle_time": to_ist(self.last_candle_time).strftime("%Y-%m-%d %H:%M:%S IST") if self.last_candle_time else None,
            "last_tick_time": to_ist(self.last_tick_time).strftime("%Y-%m-%d %H:%M:%S IST") if self.last_tick_time else None,
            "subscribed_symbols_count": 1 if (self.live_pipeline and self.live_pipeline.feed.subscribed) else 0,
            "websocket_connected": bool(self.live_pipeline and self.live_pipeline.feed.ws_connected),
            "subscribed": bool(self.live_pipeline and self.live_pipeline.feed.subscribed),
            "fresh_tick_received": bool(self.live_pipeline and self.live_pipeline.feed.tick_fresh),
            "fyers_auth_reason": self.fyers_auth.status()["reason"],
            "live_feed": self.get_live_feed_status(),
            "anomalies": dict(self.candle_anomaly_stats),
            "execution_mode": self.execution_mode,
        }

    async def process_live_candle(self, candle: Candle, source: str = "REPLAY") -> List[OmsOrder]:
        """
        Process a real-time 1-minute candlestick from live market data or paper replay.

        Strictly enforces:
        - Bar-level deduplication and out-of-order detection.
        - Staleness check (flagging if candle age is excessive).
        - Passing candle to NiftyOrbStrategy.on_candle().
        - Pre-trade risk gating via RiskEngine.
        - Paper order submission via OMS & PaperBrokerClient.
        - Lookahead-free execution with 0.50 pts slippage and statutory costs.
        - Two-phase reversal sequencing and mandatory 15:10 square-off.
        """
        if self.strategy is None:
            self.strategy = NiftyOrbStrategy(
                strategy_config=self.active_strategy_config or StrategyConfig(),
                risk_config=self.risk_config,
                contract_resolver=self.contract_resolver,
            )
            self.strategy.on_session_start(self.active_session.session_id, self.active_session.trading_date)

        # 1. Deduplication and sequence order check
        if self.last_candle_time is not None:
            if candle.timestamp == self.last_candle_time:
                self.candle_anomaly_stats["duplicate"] += 1
                logger.warning(
                    f"[MarketData] Duplicate candle detected at {candle.timestamp}. Dropping fail-closed."
                )
                return []
            elif candle.timestamp < self.last_candle_time:
                self.candle_anomaly_stats["out_of_order"] += 1
                logger.warning(
                    f"[MarketData] Out-of-order candle: {candle.timestamp} < {self.last_candle_time}. Dropping fail-closed."
                )
                return []
            elif (candle.timestamp - self.last_candle_time).total_seconds() > 60:
                self.candle_anomaly_stats["missing"] += 1

        # 2. Staleness check
        now = now_utc()
        candle_utc = to_utc(candle.timestamp)
        age_seconds = (now - candle_utc).total_seconds()
        now_ist = to_ist(now)
        is_market_hours = now_ist.weekday() < 5 and (time(9, 15) <= now_ist.time() <= time(15, 30))
        if is_market_hours and age_seconds > 300:
            self.candle_anomaly_stats["stale"] += 1
            logger.warning(f"[MarketData] Stale candle age {age_seconds:.1f}s at {candle.timestamp}.")

        self.last_candle_time = candle.timestamp

        # Cache candle for chart rendering (live candles never mix with synthetic/replay cache)
        cache = self.live_candles if source == "LIVE" else self.sample_candles
        if candle.symbol not in cache:
            cache[candle.symbol] = []
        cache[candle.symbol].append(candle)

        # 3. Strategy evaluation
        signals = self.strategy.on_candle(candle)

        if self.strategy.range_tracker.is_established:
            self.active_opening_range = self.strategy.range_tracker.to_domain_model()

        generated_orders: List[OmsOrder] = []

        # 4. Execute breakout / exit signals
        for sig in signals:
            orders = await self._execute_signal(sig, candle.timestamp)
            generated_orders.extend(orders)

        # 5. Intraday MTM & Risk Circuit Breakers
        risk_snap = self.risk_engine.get_snapshot()
        risk_signals = self.strategy.on_mtm_update(
            realized_pnl=risk_snap.realized_pnl,
            unrealized_pnl=risk_snap.unrealized_pnl,
            timestamp=candle.timestamp,
        )
        for rs in risk_signals:
            orders = await self._execute_signal(rs, candle.timestamp)
            generated_orders.extend(orders)

        return generated_orders

    async def _execute_signal(self, sig: StrategySignal, timestamp: datetime) -> List[OmsOrder]:
        """Convert a StrategySignal into an OrderIntent and submit to OMS."""
        orders: List[OmsOrder] = []
        if sig.signal_type in {SignalType.ENTER_LONG_CE, SignalType.ENTER_LONG_PE}:
            contract = sig.target_contract
            if not contract and self.contract_resolver:
                opt_type = OptionType.CE if sig.signal_type == SignalType.ENTER_LONG_CE else OptionType.PE
                try:
                    contract = self.contract_resolver.resolve_atm_contract(
                        underlying=self.active_strategy_config.underlying_symbol
                        if self.active_strategy_config
                        else "NSE:NIFTY50-INDEX",
                        spot_price=sig.underlying_price,
                        option_type=opt_type,
                        session_date=to_ist(timestamp).date(),
                    )
                except Exception as e:
                    logger.error(f"[WorkstationService] Contract resolution failed for {sig}: {e}")
                    return []

            if not contract:
                logger.error(f"[WorkstationService] No contract resolved for signal {sig.signal_id}")
                return []

            qty = (contract.lot_size if contract else 25) * (self.active_strategy_config.lots if self.active_strategy_config else 1)
            intent = OrderIntent(
                intent_id=f"INT-{uuid4().hex[:8].upper()}",
                client_order_id=f"ORD-{uuid4().hex[:8].upper()}",
                session_id=self.active_session.session_id if self.active_session else "SESSION-DEFAULT",
                strategy_name="NIFTY_ORB",
                strategy_version=self.active_strategy_config.version if hasattr(self.active_strategy_config, "version") else 1,
                symbol=contract.symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                product_type=ProductType.INTRADAY,
                qty=qty,
                tag="ORB_ENTRY",
                created_at=to_utc(timestamp),
            )
            order = await self.oms.submit_order_intent(intent, contract=contract, evaluation_time=timestamp)
            orders.append(order)

            # In paper mode, order is filled immediately by PaperBrokerClient
            if order.fills and self.strategy:
                for f in order.fills:
                    rep_sigs = self.strategy.on_order_fill(f)
                    for rep in rep_sigs:
                        rep_orders = await self._execute_signal(rep, timestamp)
                        orders.extend(rep_orders)

        elif sig.signal_type in {SignalType.CLOSE_CE, SignalType.CLOSE_PE, SignalType.FORCE_SQUARE_OFF}:
            target_sym = sig.target_contract.symbol if sig.target_contract else None
            positions_to_close = []
            for p in self.oms.get_all_positions():
                if p.is_open and p.net_qty != 0:
                    if target_sym is None or p.symbol == target_sym:
                        positions_to_close.append(p)

            for p in positions_to_close:
                exit_side = OrderSide.SELL if p.net_qty > 0 else OrderSide.BUY
                qty = abs(p.net_qty)
                contract = self.instrument_registry.get_contract_by_symbol(p.symbol)

                exit_tag = "REVERSAL_EXIT" if "reversal" in sig.reason.lower() else (
                    "SQUARE_OFF" if "square" in sig.reason.lower() else "RISK_EXIT"
                )
                intent = OrderIntent(
                    intent_id=f"INT-EXIT-{uuid4().hex[:8].upper()}",
                    client_order_id=f"ORD-EXIT-{uuid4().hex[:8].upper()}",
                    session_id=self.active_session.session_id if self.active_session else "SESSION-DEFAULT",
                    strategy_name="NIFTY_ORB",
                    strategy_version=self.active_strategy_config.version if hasattr(self.active_strategy_config, "version") else 1,
                    symbol=p.symbol,
                    side=exit_side,
                    order_type=OrderType.MARKET,
                    product_type=ProductType.INTRADAY,
                    qty=qty,
                    tag=exit_tag[:20],
                    created_at=to_utc(timestamp),
                )
                order = await self.oms.submit_order_intent(intent, contract=contract, evaluation_time=timestamp)
                orders.append(order)

                if order.fills and self.strategy:
                    for f in order.fills:
                        rep_sigs = self.strategy.on_order_fill(f)
                        for rep in rep_sigs:
                            rep_orders = await self._execute_signal(rep, timestamp)
                            orders.extend(rep_orders)

        return orders

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
            profit_protection_trigger=self.risk_engine.risk_config.profit_protection_trigger,
            profit_protection_min_lock=self.risk_engine.risk_config.profit_protection_min_lock,
            profit_step=self.risk_engine.risk_config.profit_trailing_step_profit,
            profit_lock_increment=self.risk_engine.risk_config.profit_trailing_step_lock,
            open_positions_count=open_count,
            is_data_stale=False,
            is_synthetic=not self.live_data_connected and not self.historical_replay_active,
            data_source="LIVE" if self.live_data_connected else ("HISTORICAL" if self.historical_replay_active else "SYNTHETIC"),
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
                    is_synthetic=not self.live_data_connected and not self.historical_replay_active,
                    data_source="LIVE" if self.live_data_connected else ("HISTORICAL" if self.historical_replay_active else "SYNTHETIC"),
                )
            )
        return sorted(orders, key=lambda x: x.submitted_at, reverse=True)

    def list_positions(self) -> List[PositionResponseItem]:
        """Return list of all current positions."""
        positions: List[PositionResponseItem] = []
        data_source = "LIVE" if self.live_data_connected else ("HISTORICAL" if self.historical_replay_active else "SYNTHETIC")
        is_synthetic = not self.live_data_connected and not self.historical_replay_active
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
                    is_synthetic=is_synthetic,
                    data_source=data_source,
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
            is_synthetic=not self.live_data_connected and not self.historical_replay_active,
            data_source="LIVE" if self.live_data_connected else ("HISTORICAL" if self.historical_replay_active else "SYNTHETIC"),
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

        is_historical_spot = req.dataset_id == "HISTORICAL_SPOT_8D"
        filtered_spot: List[Candle] = []

        # Check date range validity
        if is_synthetic_request:
            # Synthetic dataset is strictly available for 2026-11-06
            if req.start_date > date(2026, 11, 6) or req.end_date < date(2026, 11, 6):
                raise ValueError(
                    f"Synthetic dataset '{req.dataset_id}' is only available for 2026-11-06. "
                    f"Requested range ({req.start_date} to {req.end_date}) contains no data."
                )
        elif is_historical_spot:
            csv_path = Path("data/historical/real_nifty_spot_8d.csv")
            if not csv_path.exists():
                raise ValueError("Historical spot dataset 'real_nifty_spot_8d.csv' not found.")
            df = pd.read_csv(csv_path)
            validator = OfflineDatasetValidator()
            report, all_spot = validator.validate_index_dataframe(df, source_name="real_nifty_spot_8d.csv")
            filtered_spot = [
                c for c in all_spot
                if req.start_date <= to_ist(c.timestamp).date() <= req.end_date
            ]
            if not filtered_spot:
                raise ValueError(
                    f"Historical dataset 'HISTORICAL_SPOT_8D' has data from 2026-10-22 to 2026-11-02. "
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

        if is_historical_spot:
            backtester = UnderlyingPointBacktester(strategy_config)
            bt_res = backtester.run(filtered_spot)
            raw_trades = bt_res.get("trades", [])

            completed_trades: List[BacktestTrade] = []
            lot_sz = strategy_config.lot_size
            qty = strategy_config.lots * lot_sz
            for tr in raw_trades:
                entry_date_obj = datetime.strptime(tr["date"], "%Y-%m-%d").date()
                entry_time_obj = datetime.strptime(tr["entry_time"], "%H:%M:%S").time()
                exit_time_obj = datetime.strptime(tr["exit_time"], "%H:%M:%S").time()

                entry_dt = to_utc(IST_TZ.localize(datetime.combine(entry_date_obj, entry_time_obj)))
                exit_dt = to_utc(IST_TZ.localize(datetime.combine(entry_date_obj, exit_time_obj)))

                entry_p = Decimal(str(tr["entry_price"]))
                exit_p = Decimal(str(tr["exit_price"]))
                pnl_pts = Decimal(str(tr["pnl_points"]))
                gross_pnl = pnl_pts * Decimal(str(qty))
                costs = exec_config.brokerage_per_order * Decimal("2")
                net_pnl = gross_pnl - costs

                completed_trades.append(
                    BacktestTrade(
                        trade_id=f"TRD-{uuid4().hex[:8].upper()}",
                        symbol="NSE:NIFTY50-INDEX",
                        underlying_symbol="NSE:NIFTY50-INDEX",
                        option_type=OptionType.CE if tr["direction"] == "LONG" else OptionType.PE,
                        strike=entry_p,
                        direction=tr["direction"],
                        entry_time=entry_dt,
                        exit_time=exit_dt,
                        entry_price=entry_p,
                        exit_price=exit_p,
                        qty=qty,
                        lot_size=lot_sz,
                        gross_pnl=gross_pnl.quantize(Decimal("0.01")),
                        trading_costs=costs.quantize(Decimal("0.01")),
                        net_pnl=net_pnl.quantize(Decimal("0.01")),
                        exit_reason=tr["exit_reason"],
                        strategy_version=getattr(strategy_config, "version", 1),
                    )
                )

            calc = MetricsCalculator(initial_capital=req.initial_capital)
            metrics = calc.compute(completed_trades)
            run_result = BacktestRun(
                run_id=f"BRUN-{uuid4().hex[:8].upper()}",
                strategy_name="NIFTY_ORB_UNDERLYING",
                strategy_version=getattr(strategy_config, "version", 1),
                strategy_config_snapshot=strategy_config.model_dump(mode="json"),
                dataset_id=req.dataset_id,
                start_date=req.start_date,
                end_date=req.end_date,
                execution_config=exec_config,
                metrics=metrics,
                diagnostics=BacktestDiagnostics(),
                trades=completed_trades,
                is_synthetic=False,
                is_underlying_only=True,
                initial_capital=req.initial_capital,
                dataset_provenance="GENUINE_SPOT_1M (Yahoo Finance / Free Public Feed)",
                dataset_fingerprint="HISTORICAL_8D_NIFTY50",
            )
            self.sample_backtests[run_result.run_id] = run_result
            if self.repo:
                await self.repo.save_backtest_run(run_result)
            return self._format_backtest_details(run_result)

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
                        is_synthetic=not self.live_data_connected and not self.historical_replay_active,
            data_source="LIVE" if self.live_data_connected else ("HISTORICAL" if self.historical_replay_active else "SYNTHETIC"),
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

        # 1. Trade-level Net P&L Mean / Stdev Ratio (descriptive trade consistency metric)
        trade_pnl_ratio: Optional[Decimal] = None
        if len(run.trades) > 1:
            try:
                trade_pnls = [float(t.net_pnl) for t in run.trades]
                mean_pnl = statistics.mean(trade_pnls)
                stdev_pnl = statistics.stdev(trade_pnls)
                if stdev_pnl > 0:
                    trade_pnl_ratio = (Decimal(str(mean_pnl)) / Decimal(str(stdev_pnl))).quantize(Decimal("0.01"))
            except Exception:
                trade_pnl_ratio = None

        # 2. Portfolio-Return Annualized Sharpe Ratio (Option A when >= 2 daily periods, Option B when unavailable)
        sharpe: Optional[Decimal] = None
        sharpe_reason: Optional[str] = None

        daily_pnls = list(m.daily_pnl.values())
        if len(daily_pnls) < 2:
            sharpe = None
            sharpe_reason = (
                f"Insufficient daily return observations (minimum 2 trading days required; found {len(daily_pnls)})"
            )
        else:
            try:
                capital = getattr(run, "initial_capital", Decimal("100000.00")) or Decimal("100000.00")
                daily_returns: List[float] = []
                running_cap = capital
                for pnl in daily_pnls:
                    if running_cap > Decimal("0.00"):
                        ret = float(pnl / running_cap)
                    else:
                        ret = 0.0
                    daily_returns.append(ret)
                    running_cap += pnl

                mean_ret = statistics.mean(daily_returns)
                stdev_ret = statistics.stdev(daily_returns)
                if stdev_ret > 0:
                    ann_sharpe = (mean_ret / stdev_ret) * (252.0 ** 0.5)
                    sharpe = Decimal(str(ann_sharpe)).quantize(Decimal("0.01"))
                else:
                    sharpe = None
                    sharpe_reason = "Zero variance in daily portfolio returns"
            except Exception as e:
                sharpe = None
                sharpe_reason = f"Sharpe calculation error: {str(e)}"

        return BacktestSummaryResponse(
            run_id=run.run_id,
            dataset_id=run.dataset_id,
            start_date=run.start_date,
            end_date=run.end_date,
            strategy_name=run.strategy_name,
            strategy_version=run.strategy_version,
            is_synthetic=getattr(run, "is_synthetic", True),
            is_underlying_only=getattr(run, "is_underlying_only", False),
            provenance=getattr(run, "dataset_provenance", None),
            net_profit=m.net_pnl,
            total_trades=m.total_trades,
            win_rate_pct=m.win_rate,
            profit_factor=m.profit_factor,
            max_drawdown_amount=m.max_drawdown,
            max_drawdown_pct=m.max_drawdown_pct,
            sharpe_ratio=sharpe,
            sharpe_ratio_reason=sharpe_reason,
            trade_pnl_mean_std_ratio=trade_pnl_ratio,
            expectancy=m.expectancy,
            created_at=run.created_at,
        )

    def _format_backtest_details(self, run: BacktestRun) -> BacktestDetailsResponse:
        summary = self._format_backtest_summary(run)
        equity_curve: List[BacktestEquityPoint] = []
        running_equity = getattr(run, "initial_capital", Decimal("100000.00")) or Decimal("100000.00")
        peak_equity = running_equity

        start_dt = to_utc(IST_TZ.localize(datetime.combine(run.start_date, time(9, 15, 0))))
        equity_curve.append(
            BacktestEquityPoint(
                timestamp=start_dt,
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
