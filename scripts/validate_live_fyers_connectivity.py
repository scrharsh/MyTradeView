"""REAL FYERS connectivity validation (no mocks, no synthetic data, no replay).

Prerequisites (see docs in the Phase report):
  1. FYERS_CLIENT_ID / FYERS_SECRET_KEY / FYERS_REDIRECT_URI set in .env
  2. A valid daily access token stored via the app's OAuth flow
     (Settings -> FYERS Live Market Data), or FYERS_ACCESS_TOKEN set locally.
  3. Run during NSE market hours (Mon-Fri 09:15-15:30 IST) to receive ticks.

Usage:
    .venv\\Scripts\\python scripts/validate_live_fyers_connectivity.py [--seconds 150] [--test-reconnect]

Each check is reported PASS / FAIL / BLOCKED / NOT TESTED with IST timestamps and
evidence. Tokens, secrets and auth codes are never printed. This script places NO
orders and does not touch the strategy, OMS or database.
"""

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List

import dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
dotenv.load_dotenv(ROOT / ".env")

from src.broker.fyers_auth import FyersAuthManager
from src.common.time_utils import IST_TZ, now_utc, to_ist
from src.data.candle_builder import TickCandleBuilder
from src.data.fyers_live_feed import FyersLiveFeed

SYMBOL = os.environ.get("STRATEGY_UNDERLYING_SYMBOL", "NSE:NIFTY50-INDEX")
results = {}


def stamp() -> str:
    return datetime.now(IST_TZ).strftime("%H:%M:%S IST")


def report(key: str, status: str, evidence: str) -> None:
    results[key] = (status, evidence)
    print(f"[{stamp()}] [{status}] {key}: {evidence}")


def market_open_now() -> bool:
    n = datetime.now(IST_TZ)
    return n.weekday() < 5 and (n.hour, n.minute) >= (9, 15) and (n.hour, n.minute) < (15, 30)


@dataclass
class LiveTickMetrics:
    """Facts observed after feed validation and candle-builder processing."""

    raw_ticks_received: int = 0
    exchange_timestamps_valid: int = 0
    ticks_accepted_by_builder: int = 0
    candles_finalized: int = 0
    candles: List[object] = field(default_factory=list)


def consume_tick(builder: TickCandleBuilder, tick: object, metrics: LiveTickMetrics) -> List[object]:
    """Record a validated feed tick and preserve the builder as the acceptance authority."""
    metrics.raw_ticks_received += 1
    metrics.exchange_timestamps_valid += 1
    before = builder.stats["ticks_accepted"]
    finalized = builder.on_tick(tick)  # type: ignore[arg-type]
    metrics.ticks_accepted_by_builder += builder.stats["ticks_accepted"] - before
    metrics.candles_finalized += len(finalized)
    metrics.candles.extend(finalized)
    return finalized


def live_ticks_status(metrics: LiveTickMetrics, market_open: bool) -> str:
    """Return a truthful live-tick result; raw receipt alone can never pass."""
    if metrics.ticks_accepted_by_builder > 0:
        return "PASS"
    if metrics.raw_ticks_received > 0 and not market_open:
        return "OUTSIDE_SESSION"
    if not market_open:
        return "BLOCKED"
    return "FAIL"


async def main(seconds: int, test_reconnect: bool) -> int:
    auth = FyersAuthManager()
    print(f"[{stamp()}] Market open now: {market_open_now()}  | symbol: {SYMBOL}")

    # 1. Authentication via a real FYERS API request
    st = await auth.validate()
    if st["authenticated"]:
        report("1_AUTHENTICATION", "PASS", "FYERS profile request succeeded (token not printed).")
    elif not st["token_present"]:
        report("1_AUTHENTICATION", "BLOCKED", f"No usable daily token ({st['reason']}). Complete OAuth in the app first.")
    else:
        report("1_AUTHENTICATION", "FAIL", f"FYERS rejected the session: {st['reason']}")
    if not st["authenticated"]:
        for k in ("2_WEBSOCKET", "3_SUBSCRIPTION", "4_LIVE_TICKS", "5_CANDLES", "6_ANOMALIES", "7_RECONNECT"):
            report(k, "NOT TESTED", "Requires successful authentication.")
        return 2

    def creds():
        token = auth.get_access_token()
        return (auth.client_id, token) if token and auth.client_id else None

    feed = FyersLiveFeed(symbol=SYMBOL, credentials_provider=creds)
    builder = TickCandleBuilder(SYMBOL)
    metrics = LiveTickMetrics()
    first_ticks = []
    await feed.start()

    async def consume():
        async for ev in feed.events():
            if ev.kind == "tick" and ev.tick is not None:
                if len(first_ticks) < 3:
                    first_ticks.append(ev.tick)
                consume_tick(builder, ev.tick, metrics)
            elif ev.kind in ("disconnect", "stale", "auth_failed"):
                builder.discard_partial()

    task = asyncio.create_task(consume())
    try:
        deadline = asyncio.get_event_loop().time() + seconds
        reconnect_done = False
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1)
            if test_reconnect and not reconnect_done and feed.is_live:
                print(f"[{stamp()}] Forcing a real socket drop to test reconnection...")
                before = feed.reconnect_count
                await feed._close_socket()
                feed._mark_disconnected("MANUAL_TEST_DROP")
                reconnect_done = True
                for _ in range(60):
                    await asyncio.sleep(1)
                    if feed.reconnect_count > before and feed.is_live:
                        break
                ok = feed.reconnect_count > before and feed.ws_connected
                report("10_RECONNECT", "PASS" if (ok and feed.is_live) else ("FAIL" if not ok else "FAIL"),
                       f"reconnect_count={feed.reconnect_count}, ws_connected={feed.ws_connected}, "
                       f"subscribed={feed.subscribed}, fresh_ticks={feed.tick_fresh}")
        snap = feed.snapshot()

        report("2_WEBSOCKET", "PASS" if feed.ws_connected else "FAIL",
               f"state={snap['state']}, ws_connected={feed.ws_connected}, "
               f"connected_at={to_ist(feed.last_connect_at).strftime('%H:%M:%S') if feed.last_connect_at else None}")
        report("3_SUBSCRIPTION", "PASS" if feed.subscribed else "FAIL",
               f"acknowledged={feed.subscribed}; a received tick is not treated as an acknowledgement")
        n = snap["stats"]["raw_ticks_received"]
        if n > 0:
            report("4_RAW_TICKS", "PASS", f"raw_ticks_received={n}")
        else:
            report("4_RAW_TICKS", "BLOCKED" if not market_open_now() else "FAIL",
                   "No raw ticks received; market is closed or the feed is not delivering data.")
        valid_ts = snap["stats"]["exchange_timestamps_valid"]
        report("5_EXCHANGE_TIMESTAMPS", "PASS" if valid_ts > 0 else "NOT TESTED",
               f"valid_exchange_timestamps={valid_ts}; rejected="
               f"{snap['stats']['ticks_rejected_no_timestamp'] + snap['stats']['ticks_rejected_future_timestamp']}")
        builder_status = live_ticks_status(metrics, market_open_now())
        report("6_BUILDER_TICKS", builder_status,
               f"ticks_accepted_by_candle_builder={metrics.ticks_accepted_by_builder}; "
               f"outside_session={builder.stats['outside_session_ticks']}; "
               f"rejected_wrong_symbol={builder.stats['wrong_symbol_ticks']}; "
               f"rejected_duplicate={builder.stats['duplicate_ticks']}; "
               f"rejected_out_of_order={builder.stats['out_of_order_ticks']}")
        if n > 0:
            t = first_ticks[0]
            report("7_LIVE_TICKS", builder_status,
                   f"raw_ticks={metrics.raw_ticks_received}, exchange_timestamps_valid={metrics.exchange_timestamps_valid}, "
                   f"builder_accepted={metrics.ticks_accepted_by_builder}, outside_session={builder.stats['outside_session_ticks']}; "
                   f"first exchange_ts={to_ist(t.timestamp).strftime('%Y-%m-%d %H:%M:%S IST')}, ltp={t.ltp}; "
                   f"last_exchange_ts={to_ist(feed.last_exchange_time).strftime('%H:%M:%S IST')}")
        else:
            why = "market closed (no ticks expected)" if not market_open_now() else "no ticks within window"
            report("7_LIVE_TICKS", builder_status,
                   f"0 raw ticks received: {why}. Authentication/subscription success is NOT live-tick evidence.")
        if metrics.candles:
            c = metrics.candles[0]
            report("8_CANDLES", "PASS",
                   f"{len(metrics.candles)} finalized 1m candle(s); first {to_ist(c.timestamp).strftime('%H:%M IST')} "
                   f"O={c.open} H={c.high} L={c.low} C={c.close}")
        else:
            report("8_CANDLES", "NOT TESTED", "No candle finalized (need >=1 full minute of accepted in-session ticks).")
        report("9_ANOMALIES", "PASS" if n > 0 else "NOT TESTED",
               f"builder={builder.stats}; feed={snap['stats']}" if n > 0 else "No live data to evaluate.")
        if not test_reconnect:
            report("10_RECONNECT", "NOT TESTED", "Re-run with --test-reconnect during market hours.")
    finally:
        await feed.stop()
        task.cancel()

    print("\n=== SUMMARY ===")
    for k, (s, e) in results.items():
        print(f"{s:11s} {k}")
    return 0 if all(s in ("PASS", "NOT TESTED") for s, _ in results.values()) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=150)
    ap.add_argument("--test-reconnect", action="store_true")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.seconds, a.test_reconnect)))

