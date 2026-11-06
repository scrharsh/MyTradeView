"""Phase 8: Authentic Underlying Index Signal Backtest Runner for MyTradeView.

Executes discrete-event signal replay of the NIFTY ORB strategy across authentic
1-minute historical NIFTY 50 index candles acquired from free authorized sources.

Features:
- Preserves configured 09:16–09:45 IST opening range window.
- Enforces Monday / Thursday / Friday trading day restrictions.
- Evaluates candle-close breakouts (at 09:46+ IST) with zero lookahead bias.
- Fills pending orders at next bar open (09:47:00 IST).
- Enforces two-phase reversal sequencing.
- Executes mandatory 15:10:00 IST square-off.
- Reports underlying index point metrics only (zero fabricated option prices or delta).
"""

import sys
from datetime import date, datetime, time, timezone
from decimal import Decimal
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd
import pytz
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.risk_config import RiskConfig
from config.strategy_config import StrategyConfig
from src.common.constants import (
    BreakoutConfirmationMode,
    OptionType,
    RangeBoundaryMode,
    SignalType,
    StrategyState,
    TradingDay,
)
from src.common.time_utils import IST_TZ, to_ist, to_utc
from src.core.models import Candle
from src.data.validator import OfflineDatasetValidator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("UnderlyingBacktest")


def acquire_real_nifty_spot_candles(output_csv: Path, range_str: str = "8d") -> List[Candle]:
    """Fetch real 1-minute NIFTY cash index candles from free Yahoo Finance chart endpoint."""
    logger.info("Fetching real 1-minute NIFTY spot candles from Yahoo Finance (range=%s)...", range_str)
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI"
    params = {"interval": "1m", "range": range_str}
    headers = {"User-Agent": "Mozilla/5.0"}

    resp = requests.get(url, params=params, headers=headers, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"Yahoo Finance returned HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]

    rows = []
    for t, o, h, l, c in zip(timestamps, quote["open"], quote["high"], quote["low"], quote["close"]):
        if o is not None and h is not None and l is not None and c is not None:
            dt_ist = pd.to_datetime(t, unit="s", utc=True).astimezone(IST_TZ)
            rows.append({
                "timestamp": dt_ist.strftime("%Y-%m-%d %H:%M:%S"),
                "open": round(float(o), 2),
                "high": round(float(h), 2),
                "low": round(float(l), 2),
                "close": round(float(c), 2),
                "volume": 0,
            })

    df = pd.DataFrame(rows)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    logger.info("Saved %d authentic candles to %s", len(df), output_csv)

    validator = OfflineDatasetValidator()
    report, candles = validator.validate_index_dataframe(df, source_name=output_csv.name)
    logger.info(
        "Validation complete: %d valid records, %d gaps, %d anomalies, Usable=%s",
        report.valid_records,
        report.gap_intervals,
        report.anomalous_records,
        report.is_usable_for_backtest,
    )
    if not report.is_usable_for_backtest:
        raise ValueError(f"Spot index data failed validation: {report.diagnostic_messages}")

    return candles


class UnderlyingPointBacktester:
    """Simulates trading of the underlying index based on NIFTY ORB signals."""

    def __init__(self, strategy_config: Optional[StrategyConfig] = None) -> None:
        self.config = strategy_config or StrategyConfig(
            breakout_confirmation_mode=BreakoutConfirmationMode.CANDLE_CLOSE,
            range_boundary_mode=RangeBoundaryMode.UP_TO_0945_CLOSE,
            trading_days=[TradingDay.MONDAY.value, TradingDay.THURSDAY.value, TradingDay.FRIDAY.value],
        )
        self.risk_config = RiskConfig()

    def run(self, candles: List[Candle]) -> Dict[str, Any]:
        """Group candles by session and simulate underlying point performance."""
        from src.strategy.orb_nifty import NiftyOrbStrategy

        # Group chronologically by date
        daily_candles: Dict[date, List[Candle]] = {}
        for c in sorted(candles, key=lambda x: x.timestamp):
            d = to_ist(c.timestamp).date()
            daily_candles.setdefault(d, []).append(c)

        all_trades: List[Dict[str, Any]] = []
        session_summaries: List[Dict[str, Any]] = []

        allowed_days = set(self.config.trading_days)

        for session_date in sorted(daily_candles.keys()):
            day_bars = daily_candles[session_date]
            weekday_name = session_date.strftime("%A")
            is_allowed_day = weekday_name in self.config.trading_days

            strat = NiftyOrbStrategy(self.config, self.risk_config)
            strat.on_session_start(f"SESS_{session_date.strftime('%Y%m%d')}", session_date)

            active_position: Optional[Dict[str, Any]] = None
            pending_order: Optional[Dict[str, Any]] = None
            session_trades: List[Dict[str, Any]] = []

            for i, c in enumerate(day_bars):
                candle_ist = to_ist(c.timestamp)
                candle_time = candle_ist.time()

                # Phase 1: Lookahead-free execution of pending order at bar open
                if pending_order is not None:
                    action = pending_order["action"]
                    if action == "ENTER_LONG":
                        active_position = {
                            "direction": "LONG",
                            "entry_time": c.timestamp,
                            "entry_price": c.open,
                            "reason": pending_order["reason"],
                        }
                    elif action == "ENTER_SHORT":
                        active_position = {
                            "direction": "SHORT",
                            "entry_time": c.timestamp,
                            "entry_price": c.open,
                            "reason": pending_order["reason"],
                        }
                    elif action == "EXIT":
                        if active_position is not None:
                            entry_p = active_position["entry_price"]
                            exit_p = c.open
                            direction = active_position["direction"]
                            pnl_points = (exit_p - entry_p) if direction == "LONG" else (entry_p - exit_p)

                            trade_record = {
                                "date": session_date.strftime("%Y-%m-%d"),
                                "weekday": weekday_name,
                                "direction": direction,
                                "entry_time": to_ist(active_position["entry_time"]).strftime("%H:%M:%S"),
                                "entry_price": float(entry_p),
                                "exit_time": candle_ist.strftime("%H:%M:%S"),
                                "exit_price": float(exit_p),
                                "pnl_points": round(float(pnl_points), 2),
                                "exit_reason": pending_order["reason"],
                            }
                            session_trades.append(trade_record)
                            all_trades.append(trade_record)
                            active_position = None

                            # If this was part of a reversal, schedule the entry
                            if pending_order.get("reversal_entry"):
                                next_dir = pending_order["reversal_entry"]
                                pending_order = {
                                    "action": f"ENTER_{next_dir}",
                                    "reason": "Reversal Phase 2 Entry",
                                }
                                continue

                    pending_order = None

                # Phase 2: Ingest candle into strategy
                signals = strat.on_candle(c)

                # Phase 3: Process emitted signals
                for sig in signals:
                    if sig.signal_type == SignalType.ENTER_LONG_CE:
                        pending_order = {
                            "action": "ENTER_LONG",
                            "reason": sig.reason,
                        }
                    elif sig.signal_type == SignalType.ENTER_LONG_PE:
                        pending_order = {
                            "action": "ENTER_SHORT",
                            "reason": sig.reason,
                        }
                    elif sig.signal_type in (SignalType.CLOSE_CE, SignalType.CLOSE_PE):
                        is_reversal = "reversal" in sig.reason.lower()
                        rev_dir = "SHORT" if sig.signal_type == SignalType.CLOSE_CE else "LONG"
                        pending_order = {
                            "action": "EXIT",
                            "reason": sig.reason,
                            "reversal_entry": rev_dir if is_reversal else None,
                        }

            # End of session summary
            or_high = float(strat.range_tracker.high) if strat.range_tracker.high is not None else None
            or_low = float(strat.range_tracker.low) if strat.range_tracker.low is not None else None
            or_width = round(or_high - or_low, 2) if (or_high and or_low) else None

            session_summaries.append({
                "date": session_date.strftime("%Y-%m-%d"),
                "weekday": weekday_name,
                "is_traded_day": is_allowed_day,
                "or_high": or_high,
                "or_low": or_low,
                "or_width": or_width,
                "trades_count": len(session_trades),
                "day_pnl_points": round(sum(t["pnl_points"] for t in session_trades), 2),
            })

        # Calculate performance metrics in index points
        total_trades = len(all_trades)
        wins = [t for t in all_trades if t["pnl_points"] > 0]
        losses = [t for t in all_trades if t["pnl_points"] < 0]
        scratch = [t for t in all_trades if t["pnl_points"] == 0]

        total_points = sum(t["pnl_points"] for t in all_trades)
        win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0

        gross_gains = sum(t["pnl_points"] for t in wins)
        gross_losses = abs(sum(t["pnl_points"] for t in losses))
        profit_factor = round(gross_gains / gross_losses, 2) if gross_losses > 0 else (999.0 if gross_gains > 0 else 0.0)

        # Drawdown in points
        cum_points = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in all_trades:
            cum_points += t["pnl_points"]
            if cum_points > peak:
                peak = cum_points
            dd = peak - cum_points
            if dd > max_dd:
                max_dd = dd

        return {
            "total_sessions": len(daily_candles),
            "traded_sessions": sum(1 for s in session_summaries if s["is_traded_day"]),
            "skipped_sessions": sum(1 for s in session_summaries if not s["is_traded_day"]),
            "total_trades": total_trades,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "scratch_trades": len(scratch),
            "win_rate_pct": round(win_rate, 2),
            "total_points_pnl": round(total_points, 2),
            "gross_profit_points": round(gross_gains, 2),
            "gross_loss_points": round(gross_losses, 2),
            "profit_factor": profit_factor,
            "max_drawdown_points": round(max_dd, 2),
            "trades": all_trades,
            "session_summaries": session_summaries,
        }


def main() -> None:
    csv_path = Path("data/historical/real_nifty_spot_8d.csv")
    candles = acquire_real_nifty_spot_candles(csv_path, range_str="8d")

    backtester = UnderlyingPointBacktester()
    results = backtester.run(candles)

    print("\n" + "=" * 80)
    print("PHASE 8: REAL HISTORICAL NIFTY UNDERLYING SIGNAL BACKTEST RESULTS")
    print("=" * 80)
    print(f"Total Calendar Sessions Evaluated: {results['total_sessions']}")
    print(f"Traded Sessions (Mon/Thu/Fri):      {results['traded_sessions']}")
    print(f"Skipped Sessions (Tue/Wed):        {results['skipped_sessions']}")
    print(f"Total Underlying Trades:           {results['total_trades']}")
    print(f"Winning Trades:                    {results['winning_trades']}")
    print(f"Losing Trades:                     {results['losing_trades']}")
    print(f"Win Rate:                          {results['win_rate_pct']}%")
    print(f"Total Net Points P&L:              {results['total_points_pnl']} pts")
    print(f"Profit Factor:                     {results['profit_factor']}")
    print(f"Max Drawdown:                      {results['max_drawdown_points']} pts")

    print("\n--- DAILY SESSION BREAKDOWN ---")
    for s in results["session_summaries"]:
        status = "TRADED" if s["is_traded_day"] else "FILTERED (Non-Trading Day)"
        print(f"  {s['date']} ({s['weekday']:9}): {status:28} | OR: [{s['or_low']} - {s['or_high']}] (w={s['or_width']}) | Trades: {s['trades_count']} | Day PnL: {s['day_pnl_points']} pts")

    print("\n--- EXECUTED TRADES DETAIL ---")
    for t in results["trades"]:
        print(f"  {t['date']} {t['weekday']:6} {t['direction']:5} | Entry: {t['entry_time']} @ {t['entry_price']:.2f} -> Exit: {t['exit_time']} @ {t['exit_price']:.2f} | PnL: {t['pnl_points']:+6.2f} pts | Reason: {t['exit_reason']}")


if __name__ == "__main__":
    main()
