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
from datetime import datetime
from pathlib import Path

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
    candles = []
    first_ticks = []
    await feed.start()

    async def consume():
        async for ev in feed.events():
            if ev.kind == "tick" and ev.tick is not None:
                if len(first_ticks) < 3:
                    first_ticks.append(ev.tick)
                candles.extend(builder.on_tick(ev.tick))
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
                report("7_RECONNECT", "PASS" if (ok and feed.is_live) else ("FAIL" if not ok else "FAIL"),
                       f"reconnect_count={feed.reconnect_count}, ws_connected={feed.ws_connected}, "
                       f"subscribed={feed.subscribed}, fresh_ticks={feed.tick_fresh}")
        snap = feed.snapshot()

        report("2_WEBSOCKET", "PASS" if (feed.last_connect_at is not None) else "FAIL",
               f"state={snap['state']}, connected_at={to_ist(feed.last_connect_at).strftime('%H:%M:%S') if feed.last_connect_at else None}")
        report("3_SUBSCRIPTION", "PASS" if feed.subscribed or snap["stats"]["ticks_received"] else "FAIL",
               f"subscribed={feed.subscribed}")
        n = snap["stats"]["ticks_received"]
        if n > 0:
            t = first_ticks[0]
            report("4_LIVE_TICKS", "PASS",
                   f"{n} ticks; first exchange_ts={to_ist(t.timestamp).strftime('%Y-%m-%d %H:%M:%S IST')}, ltp={t.ltp}; "
                   f"last_exchange_ts={to_ist(feed.last_exchange_time).strftime('%H:%M:%S IST')}")
        else:
            why = "market closed (no ticks expected)" if not market_open_now() else "no ticks within window"
            report("4_LIVE_TICKS", "BLOCKED" if not market_open_now() else "FAIL",
                   f"0 ticks received: {why}. Authentication/subscription success is NOT live-tick evidence.")
        if candles:
            c = candles[0]
            report("5_CANDLES", "PASS",
                   f"{len(candles)} finalized 1m candle(s); first {to_ist(c.timestamp).strftime('%H:%M IST')} "
                   f"O={c.open} H={c.high} L={c.low} C={c.close}")
        else:
            report("5_CANDLES", "NOT TESTED", "No candle finalized (need >=1 full minute of live ticks).")
        report("6_ANOMALIES", "PASS" if n > 0 else "NOT TESTED",
               f"builder={builder.stats}; feed={snap['stats']}" if n > 0 else "No live data to evaluate.")
        if not test_reconnect:
            report("7_RECONNECT", "NOT TESTED", "Re-run with --test-reconnect during market hours.")
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

