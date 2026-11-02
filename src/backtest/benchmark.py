"""StockMock benchmark comparison interface and discrepancy evaluator."""

import csv
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.backtest.models import BacktestTrade

logger = logging.getLogger(__name__)


class BenchmarkStatus(str, Enum):
    """Comparison status against external benchmark."""
    MATCH = "MATCH"
    DEVIATION_WITHIN_TOLERANCE = "DEVIATION_WITHIN_TOLERANCE"
    MISMATCH = "MISMATCH"
    BLOCKED_BY_MISSING_DATA = "BLOCKED_BY_MISSING_DATA"


class TradeComparisonResult:
    """Individual trade comparison result against StockMock."""

    def __init__(
        self,
        trade_id: str,
        status: BenchmarkStatus,
        engine_pnl: Decimal,
        stockmock_pnl: Optional[Decimal] = None,
        pnl_diff: Optional[Decimal] = None,
        details: Optional[str] = None,
    ) -> None:
        self.trade_id = trade_id
        self.status = status
        self.engine_pnl = engine_pnl
        self.stockmock_pnl = stockmock_pnl
        self.pnl_diff = pnl_diff
        self.details = details or ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "status": self.status.value,
            "engine_pnl": str(self.engine_pnl),
            "stockmock_pnl": str(self.stockmock_pnl) if self.stockmock_pnl is not None else None,
            "pnl_diff": str(self.pnl_diff) if self.pnl_diff is not None else None,
            "details": self.details,
        }


class StockMockBenchmarkReport:
    """Consolidated benchmark verification report."""

    def __init__(
        self,
        status: BenchmarkStatus,
        total_engine_trades: int,
        total_stockmock_trades: int,
        matched_trades: int,
        engine_gross_pnl: Decimal,
        stockmock_gross_pnl: Optional[Decimal],
        pnl_difference: Optional[Decimal],
        message: str,
        trade_comparisons: List[TradeComparisonResult],
    ) -> None:
        self.status = status
        self.total_engine_trades = total_engine_trades
        self.total_stockmock_trades = total_stockmock_trades
        self.matched_trades = matched_trades
        self.engine_gross_pnl = engine_gross_pnl
        self.stockmock_gross_pnl = stockmock_gross_pnl
        self.pnl_difference = pnl_difference
        self.message = message
        self.trade_comparisons = trade_comparisons

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "total_engine_trades": self.total_engine_trades,
            "total_stockmock_trades": self.total_stockmock_trades,
            "matched_trades": self.matched_trades,
            "engine_gross_pnl": str(self.engine_gross_pnl),
            "stockmock_gross_pnl": str(self.stockmock_gross_pnl) if self.stockmock_gross_pnl is not None else None,
            "pnl_difference": str(self.pnl_difference) if self.pnl_difference is not None else None,
            "message": self.message,
            "trade_comparisons": [tc.to_dict() for tc in self.trade_comparisons],
        }


class StockMockBenchmarkComparator:
    """
    Compares MyTradeView backtest trade logs against exported StockMock benchmark trades.
    
    If no StockMock trade log CSV exists, it fails transparently with status
    BLOCKED_BY_MISSING_DATA without fabricating false historical claims.
    """

    REQUIRED_COLUMNS = {"Date", "Type", "Strike", "Entry Price", "Exit Price", "PnL"}

    def __init__(self, stockmock_csv_path: Optional[Path] = None, pnl_tolerance_pct: Decimal = Decimal("2.00")) -> None:
        self.stockmock_csv_path = stockmock_csv_path
        self.pnl_tolerance_pct = pnl_tolerance_pct

    def compare(self, engine_trades: List[BacktestTrade]) -> StockMockBenchmarkReport:
        """Compare backtest engine trades against StockMock trades."""
        if not self.stockmock_csv_path or not self.stockmock_csv_path.exists():
            return StockMockBenchmarkReport(
                status=BenchmarkStatus.BLOCKED_BY_MISSING_DATA,
                total_engine_trades=len(engine_trades),
                total_stockmock_trades=0,
                matched_trades=0,
                engine_gross_pnl=sum((t.gross_pnl for t in engine_trades), Decimal("0.00")),
                stockmock_gross_pnl=None,
                pnl_difference=None,
                message=(
                    "Historical benchmark comparison is BLOCKED: StockMock trade export CSV "
                    f"not found at {self.stockmock_csv_path}. To calibrate against StockMock, "
                    "export trade-level results and place the file in the designated path."
                ),
                trade_comparisons=[],
            )

        # Parse CSV
        stockmock_trades: List[Dict[str, Any]] = []
        try:
            with open(self.stockmock_csv_path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                headers = set(reader.fieldnames or [])
                if not self.REQUIRED_COLUMNS.issubset(headers):
                    missing = self.REQUIRED_COLUMNS - headers
                    return StockMockBenchmarkReport(
                        status=BenchmarkStatus.BLOCKED_BY_MISSING_DATA,
                        total_engine_trades=len(engine_trades),
                        total_stockmock_trades=0,
                        matched_trades=0,
                        engine_gross_pnl=sum((t.gross_pnl for t in engine_trades), Decimal("0.00")),
                        stockmock_gross_pnl=None,
                        pnl_difference=None,
                        message=f"StockMock CSV missing required columns: {missing}",
                        trade_comparisons=[],
                    )

                for row in reader:
                    stockmock_trades.append({
                        "date": row["Date"],
                        "type": row["Type"].upper(),
                        "strike": Decimal(row["Strike"]),
                        "entry_price": Decimal(row["Entry Price"]),
                        "exit_price": Decimal(row["Exit Price"]),
                        "pnl": Decimal(row["PnL"]),
                    })
        except Exception as e:
            logger.error(f"Error parsing StockMock CSV: {e}")
            return StockMockBenchmarkReport(
                status=BenchmarkStatus.BLOCKED_BY_MISSING_DATA,
                total_engine_trades=len(engine_trades),
                total_stockmock_trades=0,
                matched_trades=0,
                engine_gross_pnl=sum((t.gross_pnl for t in engine_trades), Decimal("0.00")),
                stockmock_gross_pnl=None,
                pnl_difference=None,
                message=f"Failed to read StockMock CSV: {e}",
                trade_comparisons=[],
            )

        # Compare trade by trade
        comparisons: List[TradeComparisonResult] = []
        matched = 0
        stockmock_gross = sum((t["pnl"] for t in stockmock_trades), Decimal("0.00"))
        engine_gross = sum((t.gross_pnl for t in engine_trades), Decimal("0.00"))

        for i, eng_trade in enumerate(engine_trades):
            if i < len(stockmock_trades):
                sm_trade = stockmock_trades[i]
                diff = eng_trade.gross_pnl - sm_trade["pnl"]
                rel_diff = (abs(diff) / abs(sm_trade["pnl"]) * Decimal("100.00")) if sm_trade["pnl"] != 0 else Decimal("0.00")
                if diff == Decimal("0.00"):
                    st = BenchmarkStatus.MATCH
                elif rel_diff <= self.pnl_tolerance_pct:
                    st = BenchmarkStatus.DEVIATION_WITHIN_TOLERANCE
                else:
                    st = BenchmarkStatus.MISMATCH

                comparisons.append(TradeComparisonResult(
                    trade_id=eng_trade.trade_id,
                    status=st,
                    engine_pnl=eng_trade.gross_pnl,
                    stockmock_pnl=sm_trade["pnl"],
                    pnl_diff=diff,
                    details=f"Engine: {eng_trade.gross_pnl}, SM: {sm_trade['pnl']}, RelDiff: {rel_diff.quantize(Decimal('0.01'))}%",
                ))
                matched += 1
            else:
                comparisons.append(TradeComparisonResult(
                    trade_id=eng_trade.trade_id,
                    status=BenchmarkStatus.MISMATCH,
                    engine_pnl=eng_trade.gross_pnl,
                    details="Unmatched extra trade in engine output",
                ))

        overall_status = BenchmarkStatus.MATCH
        if any(c.status == BenchmarkStatus.MISMATCH for c in comparisons) or len(engine_trades) != len(stockmock_trades):
            overall_status = BenchmarkStatus.MISMATCH
        elif any(c.status == BenchmarkStatus.DEVIATION_WITHIN_TOLERANCE for c in comparisons):
            overall_status = BenchmarkStatus.DEVIATION_WITHIN_TOLERANCE

        return StockMockBenchmarkReport(
            status=overall_status,
            total_engine_trades=len(engine_trades),
            total_stockmock_trades=len(stockmock_trades),
            matched_trades=matched,
            engine_gross_pnl=engine_gross,
            stockmock_gross_pnl=stockmock_gross,
            pnl_difference=engine_gross - stockmock_gross,
            message=f"Benchmark comparison finished with status {overall_status.value}.",
            trade_comparisons=comparisons,
        )
