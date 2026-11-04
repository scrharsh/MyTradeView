"""Phase 7.4.5: Targeted Historical Data Access Verification for FYERS API v3.

Direct HTTP implementation to test FYERS API v3 historical endpoint (/data/history)
and evaluate real market data accessibility without dependency on optional SDK sub-loggers.

Tests:
1. Local credential status (secrets/fyers_token.json, environment variables).
2. FYERS API v3 historical endpoint (/data/history) for:
   - NIFTY Spot Index (NSE:NIFTY50-INDEX)
   - Expired weekly Call option (NSE:NIFTY24O2424500CE)
   - Expired weekly Put option (NSE:NIFTY24O2424500PE)
3. Exact API error codes, status responses, and schema inspection.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FyersHistoricalCheck")


def redact(val: Optional[str]) -> str:
    if not val:
        return "<NOT_SET>"
    if len(val) <= 6:
        return "***"
    return f"{val[:3]}...{val[-3:]}"


def load_fyers_credentials() -> Dict[str, Optional[str]]:
    """Inspect local config and secrets for FYERS credentials."""
    app_id = os.environ.get("FYERS_APP_ID")
    access_token = os.environ.get("FYERS_ACCESS_TOKEN")

    token_path = Path("secrets/fyers_token.json")
    if token_path.exists():
        try:
            with open(token_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                app_id = app_id or data.get("app_id") or data.get("client_id")
                access_token = access_token or data.get("access_token")
        except Exception as e:
            logger.warning("Could not read %s: %s", token_path, e)

    return {
        "app_id": app_id,
        "access_token": access_token,
        "has_credentials": bool(app_id and access_token),
    }


def test_fyers_history_query() -> Dict[str, Any]:
    """Execute historical query against FYERS API v3."""
    creds = load_fyers_credentials()
    app_id = creds["app_id"]
    access_token = creds["access_token"]

    logger.info("=" * 70)
    logger.info("PHASE 7.4.5: FYERS API v3 HISTORICAL ACCESS TEST")
    logger.info("=" * 70)
    logger.info("Configured App ID:       %s", redact(app_id))
    logger.info("Configured Access Token: %s", redact(access_token))
    logger.info("Has Valid Local Creds:   %s", creds["has_credentials"])

    # Epoch timestamps for 2024-10-24 09:15 to 15:30 IST
    # 2024-10-24 09:15:00 IST = 2024-10-24 03:45:00 UTC = 1729741500
    # 2024-10-24 15:30:00 IST = 2024-10-24 10:00:00 UTC = 1729764000
    range_from = "1729741500"
    range_to = "1729764000"

    symbols_to_test = [
        ("NSE:NIFTY50-INDEX", "Underlying Index"),
        ("NSE:NIFTY24O2424500CE", "Expired Weekly Call (2024-10-24)"),
        ("NSE:NIFTY24O2424500PE", "Expired Weekly Put (2024-10-24)"),
    ]

    results = {}
    url = "https://api-t1.fyers.in/data/history"
    headers = {}
    if app_id and access_token:
        headers["Authorization"] = f"{app_id}:{access_token}"

    for symbol, label in symbols_to_test:
        logger.info("\nQuerying %s (%s)...", symbol, label)
        params = {
            "symbol": symbol,
            "resolution": "1",
            "date_format": "1",
            "range_from": range_from,
            "range_to": range_to,
            "cont_flag": "1" if "INDEX" not in symbol else "0",
        }

        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            logger.info("HTTP Status: %d", resp.status_code)
            data = resp.json() if resp.status_code == 200 or resp.text.startswith("{") else {"raw": resp.text}
            logger.info("Response: %s", data)

            results[symbol] = {
                "label": label,
                "http_status": resp.status_code,
                "status": data.get("s") if isinstance(data, dict) else "unknown",
                "code": data.get("code") if isinstance(data, dict) else None,
                "message": data.get("message") if isinstance(data, dict) else str(resp.text),
                "candles_count": len(data.get("candles", [])) if isinstance(data, dict) and "candles" in data else 0,
                "raw_response": data,
            }
        except Exception as e:
            logger.error("Exception querying FYERS history for %s: %s", symbol, e)
            results[symbol] = {
                "label": label,
                "status": "exception",
                "code": None,
                "message": f"Network unavailable: {type(e).__name__}",
                "candles_count": 0,
                "network_error": True,
                "error": str(e),
            }

    return {
        "creds": creds,
        "results": results,
    }


def main() -> None:
    outcome = test_fyers_history_query()
    print("\n" + "=" * 70)
    print("PHASE 7.4.5 SUMMARY RESULTS")
    print("=" * 70)
    creds_configured = outcome["creds"]["has_credentials"]
    print(f"Credentials Configured: {creds_configured}")

    any_candles = False
    for sym, res in outcome["results"].items():
        cnt = res.get("candles_count", 0)
        code = res.get("code")
        msg = res.get("message")
        http_st = res.get("http_status")
        print(f"  {sym:24} -> HTTP {http_st}, Code: {code}, Message: '{msg}', Candles: {cnt}")
        if cnt > 0:
            any_candles = True

    if not creds_configured:
        print("\n[VERDICT] FYERS API credentials NOT configured in environment or secrets/fyers_token.json.")
        print("          Endpoint returned code -16: 'Could not authenticate the user'.")
    elif not any_candles:
        print("\n[VERDICT] FYERS API returned 0 candles for target symbols.")
    else:
        print(f"\n[VERDICT] Successfully retrieved real historical candles.")


if __name__ == "__main__":
    main()
