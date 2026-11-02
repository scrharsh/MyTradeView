"""Discrete-event backtesting engine module for MyTradeView."""

from src.backtest.benchmark import (
    BenchmarkStatus,
    StockMockBenchmarkComparator,
    StockMockBenchmarkReport,
    TradeComparisonResult,
)
from src.backtest.engine import DiscreteEventBacktestEngine
from src.backtest.execution import SimulatedExecutionModel
from src.backtest.metrics import MetricsCalculator
from src.backtest.models import (
    BacktestDiagnostics,
    BacktestMetrics,
    BacktestRun,
    BacktestTrade,
    ExecutionModelConfig,
)
from src.backtest.synthetic import SyntheticMarketDataBuilder

__all__ = [
    "BacktestDiagnostics",
    "BacktestMetrics",
    "BacktestRun",
    "BacktestTrade",
    "BenchmarkStatus",
    "DiscreteEventBacktestEngine",
    "ExecutionModelConfig",
    "MetricsCalculator",
    "SimulatedExecutionModel",
    "StockMockBenchmarkComparator",
    "StockMockBenchmarkReport",
    "SyntheticMarketDataBuilder",
    "TradeComparisonResult",
]
