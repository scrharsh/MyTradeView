"""Domain models and configuration schemas for the Discrete-Event Backtesting Engine."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field

from src.common.constants import OptionType
from src.common.time_utils import now_utc


class ExecutionModelConfig(BaseModel):
    """
    Configurable assumptions for simulated order execution, slippage, and statutory charges.
    """
    model_config = ConfigDict(frozen=True)

    brokerage_per_order: Decimal = Field(default=Decimal("20.00"), ge=0, description="Flat brokerage per executed order in INR")
    stt_rate: Decimal = Field(default=Decimal("0.00125"), ge=0, description="Securities Transaction Tax on sell side (0.125%)")
    exchange_turnover_rate: Decimal = Field(default=Decimal("0.00053"), ge=0, description="NSE exchange transaction charge (0.053%)")
    gst_rate: Decimal = Field(default=Decimal("0.18"), ge=0, description="GST on brokerage and exchange charges (18%)")
    sebi_charges_rate: Decimal = Field(default=Decimal("0.000001"), ge=0, description="SEBI regulatory fee")
    stamp_duty_rate: Decimal = Field(default=Decimal("0.00003"), ge=0, description="Stamp duty on buy side (0.003%)")
    slippage_points: Decimal = Field(default=Decimal("0.50"), ge=0, description="Execution slippage in points added to buy / subtracted from sell")
    slippage_model: str = Field(default="fixed_points", description="fixed_points or percentage")
    fill_timing: str = Field(default="next_bar_open", description="next_bar_open (lookahead-free) or current_bar_close")
    partial_fill_supported: bool = Field(default=False, description="Whether partial fills are enabled in simulation")


class BacktestTrade(BaseModel):
    """
    Execution record for a completed round-trip trade in a backtest simulation.
    """
    model_config = ConfigDict(frozen=True)

    trade_id: str = Field(default_factory=lambda: f"TRD_{uuid4().hex[:10]}")
    symbol: str
    underlying_symbol: str = "NSE:NIFTY50-INDEX"
    option_type: OptionType
    strike: Decimal
    direction: str = "BUY"
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    qty: int
    lot_size: int
    gross_pnl: Decimal
    trading_costs: Decimal
    net_pnl: Decimal
    exit_reason: str
    strategy_version: int = 1
    data_quality_warnings: List[str] = Field(default_factory=list)


class BacktestMetrics(BaseModel):
    """
    Comprehensive quantitative performance and risk metrics.
    """
    model_config = ConfigDict(frozen=True)

    gross_pnl: Decimal
    net_pnl: Decimal
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal
    profit_factor: Decimal
    avg_win: Decimal
    avg_loss: Decimal
    expectancy: Decimal
    max_drawdown: Decimal
    max_drawdown_pct: Decimal
    daily_pnl: Dict[str, Decimal] = Field(default_factory=dict)
    monthly_pnl: Dict[str, Decimal] = Field(default_factory=dict)
    equity_curve: List[Dict[str, Any]] = Field(default_factory=list)


class BacktestDiagnostics(BaseModel):
    """
    Diagnostic counters tracking execution edge cases and risk triggers.
    """
    model_config = ConfigDict(frozen=True)

    daily_loss_limit_hits: int = 0
    profit_protection_hits: int = 0
    reversals_count: int = 0
    square_off_count: int = 0
    rejected_orders_count: int = 0
    unfilled_orders_count: int = 0
    partial_fills_count: int = 0
    missing_data_events: int = 0
    ambiguous_events_count: int = 0


class BacktestRun(BaseModel):
    """
    Authoritative, immutable backtest run artifact.
    """
    model_config = ConfigDict(frozen=True)

    run_id: str = Field(default_factory=lambda: f"BT_{datetime.now().strftime('%Y%m%d')}_{uuid4().hex[:8]}")
    dataset_id: str
    dataset_fingerprint: Optional[str] = None
    dataset_provenance: Optional[str] = None
    is_synthetic: bool = False
    is_underlying_only: bool = False
    start_date: date
    end_date: date
    strategy_name: str
    strategy_version: int
    strategy_config_snapshot: Dict[str, Any]
    execution_config: ExecutionModelConfig
    engine_version: str = "1.0.0"
    initial_capital: Decimal = Decimal("100000.00")
    metrics: BacktestMetrics
    diagnostics: BacktestDiagnostics
    trades: List[BacktestTrade]
    created_at: datetime = Field(default_factory=now_utc)
