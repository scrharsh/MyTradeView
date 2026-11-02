"""Quantitative performance and risk metrics calculator for backtesting."""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from src.backtest.models import BacktestMetrics, BacktestTrade


class MetricsCalculator:
    """
    Computes rigorous, deterministic quantitative performance and risk metrics
    for backtest trade histories with exact Decimal precision.
    """

    def __init__(self, initial_capital: Decimal = Decimal("100000.00")) -> None:
        self.initial_capital = initial_capital

    def compute(self, trades: List[BacktestTrade]) -> BacktestMetrics:
        """
        Calculate full performance report from completed round-trip trades.
        """
        if not trades:
            return BacktestMetrics(
                gross_pnl=Decimal("0.00"),
                net_pnl=Decimal("0.00"),
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=Decimal("0.00"),
                profit_factor=Decimal("0.00"),
                avg_win=Decimal("0.00"),
                avg_loss=Decimal("0.00"),
                expectancy=Decimal("0.00"),
                max_drawdown=Decimal("0.00"),
                max_drawdown_pct=Decimal("0.00"),
                daily_pnl={},
                monthly_pnl={},
                equity_curve=[],
            )

        total_trades = len(trades)
        winning_trades_list = [t for t in trades if t.net_pnl > 0]
        losing_trades_list = [t for t in trades if t.net_pnl < 0]

        winning_trades = len(winning_trades_list)
        losing_trades = len(losing_trades_list)

        gross_pnl = sum((t.gross_pnl for t in trades), Decimal("0.00")).quantize(Decimal("0.01"))
        net_pnl = sum((t.net_pnl for t in trades), Decimal("0.00")).quantize(Decimal("0.01"))

        win_rate = (
            (Decimal(winning_trades) / Decimal(total_trades) * Decimal("100.00")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if total_trades > 0
            else Decimal("0.00")
        )

        gross_profit = sum((t.gross_pnl for t in trades if t.gross_pnl > 0), Decimal("0.00"))
        gross_loss = abs(sum((t.gross_pnl for t in trades if t.gross_pnl < 0), Decimal("0.00")))

        if gross_loss == Decimal("0.00"):
            profit_factor = Decimal("999.99") if gross_profit > 0 else Decimal("0.00")
        else:
            profit_factor = (gross_profit / gross_loss).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        avg_win = (
            (
                sum((t.net_pnl for t in winning_trades_list), Decimal("0.00"))
                / Decimal(winning_trades)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if winning_trades > 0
            else Decimal("0.00")
        )

        avg_loss = (
            (
                abs(sum((t.net_pnl for t in losing_trades_list), Decimal("0.00")))
                / Decimal(losing_trades)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if losing_trades > 0
            else Decimal("0.00")
        )

        expectancy = (net_pnl / Decimal(total_trades)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Drawdown and Equity Curve computation
        cum_net_pnl = Decimal("0.00")
        peak_equity = self.initial_capital
        max_drawdown = Decimal("0.00")
        max_drawdown_pct = Decimal("0.00")
        equity_curve: List[Dict[str, Any]] = []

        # Daily and monthly aggregations
        daily_pnl: Dict[str, Decimal] = {}
        monthly_pnl: Dict[str, Decimal] = {}

        # Initial point
        equity_curve.append(
            {
                "timestamp": trades[0].entry_time.isoformat(),
                "trade_id": "INIT",
                "net_pnl": "0.00",
                "cumulative_net_pnl": "0.00",
                "equity": str(self.initial_capital),
                "drawdown": "0.00",
                "drawdown_pct": "0.00",
            }
        )

        for trade in trades:
            cum_net_pnl += trade.net_pnl
            current_equity = self.initial_capital + cum_net_pnl
            if current_equity > peak_equity:
                peak_equity = current_equity

            drawdown = peak_equity - current_equity
            dd_pct = (
                (drawdown / peak_equity * Decimal("100.00")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                if peak_equity > 0
                else Decimal("0.00")
            )

            if drawdown > max_drawdown:
                max_drawdown = drawdown
            if dd_pct > max_drawdown_pct:
                max_drawdown_pct = dd_pct

            exit_dt = trade.exit_time
            day_str = exit_dt.strftime("%Y-%m-%d")
            month_str = exit_dt.strftime("%Y-%m")

            daily_pnl[day_str] = (daily_pnl.get(day_str, Decimal("0.00")) + trade.net_pnl).quantize(
                Decimal("0.01")
            )
            monthly_pnl[month_str] = (
                monthly_pnl.get(month_str, Decimal("0.00")) + trade.net_pnl
            ).quantize(Decimal("0.01"))

            equity_curve.append(
                {
                    "timestamp": exit_dt.isoformat(),
                    "trade_id": trade.trade_id,
                    "net_pnl": str(trade.net_pnl),
                    "cumulative_net_pnl": str(cum_net_pnl),
                    "equity": str(current_equity),
                    "drawdown": str(drawdown),
                    "drawdown_pct": str(dd_pct),
                }
            )

        return BacktestMetrics(
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_win=avg_win,
            avg_loss=avg_loss,
            expectancy=expectancy,
            max_drawdown=max_drawdown.quantize(Decimal("0.01")),
            max_drawdown_pct=max_drawdown_pct.quantize(Decimal("0.01")),
            daily_pnl=daily_pnl,
            monthly_pnl=monthly_pnl,
            equity_curve=equity_curve,
        )
