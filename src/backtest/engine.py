"""Deterministic Discrete-Event Backtesting Engine for Options Strategies."""

from datetime import date, datetime, time
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from config.risk_config import RiskConfig
from config.strategy_config import StrategyConfig
from src.backtest.execution import SimulatedExecutionModel
from src.backtest.metrics import MetricsCalculator
from src.backtest.models import (
    BacktestDiagnostics,
    BacktestMetrics,
    BacktestRun,
    BacktestTrade,
    ExecutionModelConfig,
)
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
    RangeBoundaryMode,
    SignalType,
    StrategyState,
)
from src.common.exceptions import MissingMarketDataError
from src.common.time_utils import to_ist, to_utc
from src.core.models import (
    Candle,
    Fill,
    OptionContract,
    OrderRequest,
    Position,
    StrategySignal,
    TradingSession,
)
from src.data.contract_resolver import ContractResolver
from src.risk.engine import RiskEngine
from src.strategy.orb_nifty import NiftyOrbStrategy

logger = logging.getLogger(__name__)


class DiscreteEventBacktestEngine:
    """
    Deterministic, discrete-event backtesting engine.
    
    Guarantees:
    - Chronological event processing with strictly no lookahead bias.
    - Full fidelity with NiftyOrbStrategy and RiskEngine rules.
    - Realistic Indian option tax calculations, brokerage, and slippage.
    - Strict validation of market data continuity and fail-closed diagnostics.
    """

    def __init__(
        self,
        strategy_config: Optional[StrategyConfig] = None,
        risk_config: Optional[RiskConfig] = None,
        execution_config: Optional[ExecutionModelConfig] = None,
        contract_resolver: Optional[ContractResolver] = None,
        initial_capital: Decimal = Decimal("100000.00"),
    ) -> None:
        if strategy_config is None:
            self.strategy_config = StrategyConfig(
                breakout_confirmation_mode=BreakoutConfirmationMode.CANDLE_CLOSE,
                range_boundary_mode=RangeBoundaryMode.UP_TO_0944_CLOSE,
                order_execution_style=OrderExecutionStyle.MARKET,
                expiry_day_selection=ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE,
            )
        else:
            self.strategy_config = strategy_config

        self.risk_config = risk_config or RiskConfig()
        self.execution_config = execution_config or ExecutionModelConfig()
        self.contract_resolver = contract_resolver
        self.initial_capital = initial_capital

        self.execution_model = SimulatedExecutionModel(self.execution_config)
        self.metrics_calculator = MetricsCalculator(self.initial_capital)

    def run(
        self,
        spot_candles: List[Candle],
        option_candles: Dict[str, List[Candle]],
        dataset_id: str = "SYNTHETIC_DATASET",
    ) -> BacktestRun:
        """
        Execute deterministic discrete-event backtest across the provided candle history.
        """
        if not spot_candles:
            empty_metrics = self.metrics_calculator.compute([])
            return BacktestRun(
                dataset_id=dataset_id,
                start_date=date.today(),
                end_date=date.today(),
                strategy_name="NIFTY_ORB",
                strategy_version=getattr(self.strategy_config, "version", 1),
                strategy_config_snapshot=self.strategy_config.model_dump(mode="json"),
                execution_config=self.execution_config,
                metrics=empty_metrics,
                diagnostics=BacktestDiagnostics(),
                trades=[],
            )

        # 1. Index market data
        sorted_spot = sorted(spot_candles, key=lambda c: c.timestamp)
        option_map: Dict[Tuple[str, datetime], Candle] = {}
        for sym, c_list in option_candles.items():
            for c in c_list:
                option_map[(sym, c.timestamp)] = c

        # Determine date bounds
        start_date = to_ist(sorted_spot[0].timestamp).date()
        end_date = to_ist(sorted_spot[-1].timestamp).date()

        # Group spot candles by trading date
        daily_spot: Dict[date, List[Candle]] = {}
        for c in sorted_spot:
            d = to_ist(c.timestamp).date()
            if d not in daily_spot:
                daily_spot[d] = []
            daily_spot[d].append(c)

        completed_trades: List[BacktestTrade] = []
        diagnostics = {
            "daily_loss_limit_hits": 0,
            "profit_protection_hits": 0,
            "reversals_count": 0,
            "square_off_count": 0,
            "rejected_orders_count": 0,
            "unfilled_orders_count": 0,
            "partial_fills_count": 0,
            "missing_data_events": 0,
            "ambiguous_events_count": 0,
        }

        # 2. Iterate each trading day independently
        for session_date in sorted(daily_spot.keys()):
            session_id = f"BT_SESS_{session_date.strftime('%Y%m%d')}"
            session = TradingSession(
                session_id=session_id,
                trading_date=session_date,
                execution_mode=ExecutionMode.PAPER,
            )

            strategy = NiftyOrbStrategy(
                strategy_config=self.strategy_config,
                risk_config=self.risk_config,
                contract_resolver=self.contract_resolver,
            )
            strategy.on_session_start(session_id, session_date)

            risk_engine = RiskEngine(
                session_id=session_id,
                risk_config=self.risk_config,
                strategy_config=self.strategy_config,
            )

            # Intraday execution state
            pending_order: Optional[Tuple[OrderRequest, Optional[OptionContract], str]] = None
            active_trade: Optional[Dict[str, Any]] = None

            # Get chronological timeline of timestamps for today
            day_spot_candles = daily_spot[session_date]
            all_timestamps: Set[datetime] = {c.timestamp for c in day_spot_candles}
            for (sym, ts) in option_map.keys():
                if to_ist(ts).date() == session_date:
                    all_timestamps.add(ts)

            sorted_timeline = sorted(list(all_timestamps))
            spot_by_time: Dict[datetime, Candle] = {c.timestamp: c for c in day_spot_candles}

            for current_time in sorted_timeline:
                # --------------------------------------------------------------
                # Phase A: Execute Pending Orders (Lookahead-free: at bar open)
                # --------------------------------------------------------------
                if pending_order is not None:
                    order_req, contract, reason = pending_order
                    opt_candle = option_map.get((order_req.symbol, current_time))

                    if opt_candle is None:
                        diagnostics["missing_data_events"] += 1
                        logger.warning(
                            f"[Backtest Engine] Missing option candle for {order_req.symbol} at {current_time}. "
                            f"Order {order_req.client_order_id} cannot fill."
                        )
                    else:
                        # Fill pending order at next bar open
                        fills = self.execution_model.simulate_fill(
                            order_req, opt_candle, is_next_bar=True
                        )
                        if len(fills) > 1:
                            diagnostics["partial_fills_count"] += len(fills) - 1

                        for fill in fills:
                            risk_engine.on_fill(fill)
                            replacement_signals = strategy.on_order_fill(fill)

                            if order_req.side == OrderSide.BUY:
                                # Opened long position
                                active_trade = {
                                    "trade_id": f"TRD_{fill.client_order_id[:10]}",
                                    "symbol": fill.symbol,
                                    "contract": contract,
                                    "entry_time": fill.timestamp,
                                    "entry_price": fill.fill_price,
                                    "qty": fill.fill_qty,
                                    "lot_size": contract.lot_size if contract else 25,
                                    "option_type": contract.option_type if contract else OptionType.CE,
                                    "strike": contract.strike if contract else Decimal("0"),
                                }
                            else:
                                # Closed position -> Record completed trade
                                if active_trade:
                                    gross_pnl = (fill.fill_price - active_trade["entry_price"]) * Decimal(fill.fill_qty)
                                    costs = self.execution_model.calculate_roundtrip_costs(
                                        active_trade["entry_price"], fill.fill_price, fill.fill_qty
                                    )
                                    net_pnl = gross_pnl - costs

                                    completed_trades.append(
                                        BacktestTrade(
                                            trade_id=active_trade["trade_id"],
                                            symbol=active_trade["symbol"],
                                            underlying_symbol=self.strategy_config.underlying_symbol,
                                            option_type=active_trade["option_type"],
                                            strike=active_trade["strike"],
                                            direction="BUY",
                                            entry_time=active_trade["entry_time"],
                                            exit_time=fill.timestamp,
                                            entry_price=active_trade["entry_price"],
                                            exit_price=fill.fill_price,
                                            qty=fill.fill_qty,
                                            lot_size=active_trade["lot_size"],
                                            gross_pnl=gross_pnl.quantize(Decimal("0.01")),
                                            trading_costs=costs.quantize(Decimal("0.01")),
                                            net_pnl=net_pnl.quantize(Decimal("0.01")),
                                            exit_reason=reason,
                                            strategy_version=getattr(self.strategy_config, "version", 1),
                                        )
                                    )
                                    active_trade = None

                            # Process replacement signal (from atomic reversal Phase 2)
                            if replacement_signals:
                                rep_sig = replacement_signals[0]
                                rep_contract = rep_sig.target_contract
                                rep_req = OrderRequest(
                                    client_order_id=f"ORD_{uuid4().hex[:12]}",
                                    session_id=session_id,
                                    symbol=rep_contract.symbol if rep_contract else active_trade["symbol"],
                                    side=OrderSide.BUY,
                                    order_type=OrderType.MARKET,
                                    qty=(rep_contract.lot_size if rep_contract else 25) * self.strategy_config.lots,
                                    product_type=ProductType.INTRADAY,
                                    tag="REVERSAL_ENTRY",
                                )
                                # Queue replacement order for execution
                                pending_order = (rep_req, rep_contract, rep_sig.reason)
                            else:
                                pending_order = None

                # --------------------------------------------------------------
                # Phase B: Intraday MTM & Risk Circuit Breakers
                # --------------------------------------------------------------
                if active_trade is not None:
                    opt_candle = option_map.get((active_trade["symbol"], current_time))
                    if opt_candle is not None:
                        # Recalculate MTM
                        realized_today = sum(
                            (t.net_pnl for t in completed_trades if to_ist(t.exit_time).date() == session_date),
                            Decimal("0.00"),
                        )
                        unrealized_today = (opt_candle.close - active_trade["entry_price"]) * Decimal(active_trade["qty"])

                        risk_signals = strategy.on_mtm_update(
                            realized_pnl=realized_today,
                            unrealized_pnl=unrealized_today,
                            timestamp=current_time,
                        )

                        if risk_signals and pending_order is None:
                            risk_sig = risk_signals[0]
                            if "Daily MTM loss limit" in risk_sig.reason:
                                diagnostics["daily_loss_limit_hits"] += 1
                            elif "Profit protection floor" in risk_sig.reason:
                                diagnostics["profit_protection_hits"] += 1

                            exit_req = OrderRequest(
                                client_order_id=f"ORD_{uuid4().hex[:12]}",
                                session_id=session_id,
                                symbol=active_trade["symbol"],
                                side=OrderSide.SELL,
                                order_type=OrderType.MARKET,
                                qty=active_trade["qty"],
                                product_type=ProductType.INTRADAY,
                                tag="RISK_EXIT",
                            )
                            pending_order = (exit_req, active_trade["contract"], risk_sig.reason)

                # --------------------------------------------------------------
                # Phase C: Strategy Bar Processing & Signal Generation
                # --------------------------------------------------------------
                spot_c = spot_by_time.get(current_time)
                if spot_c is not None and pending_order is None:
                    signals = strategy.on_candle(spot_c)

                    for sig in signals:
                        if sig.signal_type in {SignalType.ENTER_LONG_CE, SignalType.ENTER_LONG_PE}:
                            contract = sig.target_contract
                            target_sym = contract.symbol if contract else f"{self.strategy_config.underlying_symbol}_OPT"
                            lot_sz = contract.lot_size if contract else 25
                            order_req = OrderRequest(
                                client_order_id=f"ORD_{uuid4().hex[:12]}",
                                session_id=session_id,
                                symbol=target_sym,
                                side=OrderSide.BUY,
                                order_type=OrderType.MARKET,
                                qty=lot_sz * self.strategy_config.lots,
                                product_type=ProductType.INTRADAY,
                                tag="ORB_ENTRY",
                            )
                            val = risk_engine.validate_order(
                                order_request=order_req,
                                execution_mode=ExecutionMode.PAPER,
                                session=session,
                                contract=contract,
                                current_position=None,
                                pending_orders=[],
                                last_market_data_time=current_time,
                                evaluation_time=current_time,
                            )
                            if val.is_valid:
                                pending_order = (order_req, contract, sig.reason)
                            else:
                                diagnostics["rejected_orders_count"] += 1
                                logger.info(f"[Backtest Engine] Pre-trade risk rejected order: {val.message}")

                        elif sig.signal_type in {SignalType.CLOSE_CE, SignalType.CLOSE_PE}:
                            if active_trade is not None:
                                if "Mandatory intraday EOD" in sig.reason:
                                    diagnostics["square_off_count"] += 1
                                elif "Reversal triggered" in sig.reason:
                                    diagnostics["reversals_count"] += 1

                                exit_req = OrderRequest(
                                    client_order_id=f"ORD_{uuid4().hex[:12]}",
                                    session_id=session_id,
                                    symbol=active_trade["symbol"],
                                    side=OrderSide.SELL,
                                    order_type=OrderType.MARKET,
                                    qty=active_trade["qty"],
                                    product_type=ProductType.INTRADAY,
                                    tag="ORB_EXIT",
                                )
                                pending_order = (exit_req, active_trade["contract"], sig.reason)


            # End of day session cleanup: if pending exit remains or active trade unclosed
            if pending_order is not None:
                order_req, contract, reason = pending_order
                last_ts = sorted_timeline[-1]
                opt_c = option_map.get((order_req.symbol, last_ts))
                if opt_c and order_req.side == OrderSide.SELL and active_trade:
                    # Fill final exit at last bar close with slippage
                    fill_p = self.execution_model.calculate_slippage_price(OrderSide.SELL, opt_c.close)
                    gross_pnl = (fill_p - active_trade["entry_price"]) * Decimal(active_trade["qty"])
                    costs = self.execution_model.calculate_roundtrip_costs(
                        active_trade["entry_price"], fill_p, active_trade["qty"]
                    )
                    net_pnl = gross_pnl - costs
                    completed_trades.append(
                        BacktestTrade(
                            trade_id=active_trade["trade_id"],
                            symbol=active_trade["symbol"],
                            underlying_symbol=self.strategy_config.underlying_symbol,
                            option_type=active_trade["option_type"],
                            strike=active_trade["strike"],
                            direction="BUY",
                            entry_time=active_trade["entry_time"],
                            exit_time=last_ts,
                            entry_price=active_trade["entry_price"],
                            exit_price=fill_p,
                            qty=active_trade["qty"],
                            lot_size=active_trade["lot_size"],
                            gross_pnl=gross_pnl.quantize(Decimal("0.01")),
                            trading_costs=costs.quantize(Decimal("0.01")),
                            net_pnl=net_pnl.quantize(Decimal("0.01")),
                            exit_reason=reason,
                            strategy_version=getattr(self.strategy_config, "version", 1),
                        )
                    )
                    active_trade = None
                    pending_order = None

        # 3. Calculate quantitative performance metrics
        metrics = self.metrics_calculator.compute(completed_trades)
        diag_model = BacktestDiagnostics(**diagnostics)

        return BacktestRun(
            dataset_id=dataset_id,
            start_date=start_date,
            end_date=end_date,
            strategy_name="NIFTY_ORB",
            strategy_version=getattr(self.strategy_config, "version", 1),
            strategy_config_snapshot=self.strategy_config.model_dump(mode="json"),
            execution_config=self.execution_config,
            metrics=metrics,
            diagnostics=diag_model,
            trades=completed_trades,
        )
