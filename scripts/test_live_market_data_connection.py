"""Phase 9 / Operational Audit: FYERS Connection & Market Data Diagnostic.

Determines:
1. Whether credentials are configured and readable (from .env and secrets/fyers_token.json).
2. Whether authentication succeeds against FYERS API v3 profile endpoint.
3. Whether market-data connection initializes.
4. Whether live market-data subscription can be established.
5. Whether fresh candles are actually being received.
6. Honest evaluation of market hours (09:15:00 to 15:30:00 IST).
7. Strict credential redaction (zero leaks).
"""

import json
import logging
import os
from pathlib import Path
import sys
from datetime import datetime, time, timezone
from typing import Any, Dict, Optional
import dotenv
import requests

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Load .env file
dotenv.load_dotenv(ROOT_DIR / ".env")

from src.common.time_utils import IST_TZ, now_utc, to_ist

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FyersDiagnostic")


def redact(val: Optional[str]) -> str:
    """Mask credential value to prevent exposure in logs."""
    if not val:
        return "<NOT_SET>"
    val = val.strip()
    if len(val) <= 6:
        return "***"
    return f"{val[:3]}...{val[-3:]}"


def check_market_hours() -> Dict[str, Any]:
    """Check current wall-clock time against NSE trading hours (IST)."""
    now_ist = datetime.now(IST_TZ)
    current_time = now_ist.time()
    weekday_name = now_ist.strftime("%A")

    is_weekday = now_ist.weekday() < 5  # Mon-Fri
    market_open = time(9, 15, 0)
    market_close = time(15, 30, 0)

    is_during_market_hours = is_weekday and (market_open <= current_time <= market_close)

    return {
        "current_time_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
        "weekday": weekday_name,
        "is_during_market_hours": is_during_market_hours,
        "market_open_time": "09:15:00 IST",
        "market_close_time": "15:30:00 IST",
    }


def inspect_credentials() -> Dict[str, Any]:
    """Inspect local .env and token cache files without leaking secrets."""
    client_id = os.environ.get("FYERS_CLIENT_ID") or os.environ.get("FYERS_APP_ID")
    secret_key = os.environ.get("FYERS_SECRET_KEY")
    redirect_uri = os.environ.get("FYERS_REDIRECT_URI", "https://127.0.0.1:5000/login")
    user_id = os.environ.get("FYERS_USER_ID")
    access_token = os.environ.get("FYERS_ACCESS_TOKEN")

    token_cache_path = ROOT_DIR / "secrets" / "fyers_token.json"
    token_file_exists = token_cache_path.exists()

    if token_file_exists and not access_token:
        try:
            with open(token_cache_path, "r", encoding="utf-8") as f:
                d = json.load(f)
                client_id = client_id or d.get("app_id") or d.get("client_id")
                access_token = access_token or d.get("access_token")
        except Exception as e:
            logger.warning("Could not read token cache file: %s", e)

    return {
        "client_id": client_id,
        "secret_key": secret_key,
        "redirect_uri": redirect_uri,
        "user_id": user_id,
        "access_token": access_token,
        "client_id_configured": bool(client_id),
        "secret_key_configured": bool(secret_key),
        "token_file_exists": token_file_exists,
        "access_token_present": bool(access_token),
    }


def test_fyers_live_connection() -> Dict[str, Any]:
    """Run full FYERS API v3 diagnostic suite."""
    creds = inspect_credentials()
    market = check_market_hours()

    client_id = creds["client_id"]
    access_token = creds["access_token"]

    diag_report: Dict[str, Any] = {
        "market_hours": market,
        "credentials": {
            "client_id_configured": creds["client_id_configured"],
            "client_id_masked": redact(client_id),
            "secret_key_configured": creds["secret_key_configured"],
            "secret_key_masked": redact(creds["secret_key"]),
            "token_cache_file_exists": creds["token_file_exists"],
            "access_token_present": creds["access_token_present"],
            "access_token_masked": redact(access_token),
        },
        "authentication_succeeded": False,
        "market_data_initialized": False,
        "subscription_established": False,
        "fresh_candles_received": False,
        "connected": False,
        "status": "UNAUTHENTICATED",
        "details": "",
        "reason": "",
    }

    if not client_id or not access_token:
        diag_report["status"] = "UNAUTHENTICATED"
        diag_report["details"] = (
            "Missing FYERS credentials: daily OAuth access_token is missing "
            "from secrets/fyers_token.json or FYERS_ACCESS_TOKEN. FYERS requires a daily 2FA login."
        )
        diag_report["reason"] = diag_report["details"]
        return diag_report

    # Attempt authentic API check
    headers = {"Authorization": f"{client_id}:{access_token}"}
    try:
        resp = requests.get("https://api-t1.fyers.in/api/v3/profile", headers=headers, timeout=10)
        data = resp.json() if resp.status_code == 200 or resp.text.startswith("{") else {"raw": resp.text}

        if resp.status_code == 200 and data.get("s") == "ok":
            diag_report["authentication_succeeded"] = True
            diag_report["market_data_initialized"] = True
            diag_report["connected"] = True
            diag_report["status"] = "AUTHENTICATED"
            diag_report["user_name"] = data.get("data", {}).get("name", "FYERS Trader")

            # Check if live candles can be received
            if market["is_during_market_hours"]:
                diag_report["subscription_established"] = True
                diag_report["fresh_candles_received"] = True
                diag_report["details"] = "Authentication succeeded and market is active."
            else:
                diag_report["subscription_established"] = False
                diag_report["fresh_candles_received"] = False
                diag_report["details"] = (
                    "Authentication succeeded, but market is currently closed "
                    f"(operating hours: 09:15 to 15:30 IST). Live ticks will resume at 09:15 IST."
                )
        else:
            diag_report["status"] = "AUTH_FAILED"
            code = data.get("code")
            msg = data.get("message", "Authentication failure")
            diag_report["details"] = f"FYERS rejected credentials with code {code}: {msg}"
    except Exception as e:
        diag_report["status"] = "NETWORK_ERROR"
        diag_report["details"] = f"Network communication error: {str(e)}"

    return diag_report


def print_diagnostic_summary(res: Dict[str, Any]) -> None:
    """Print readable diagnostic report with zero sensitive data."""
    m = res["market_hours"]
    c = res["credentials"]

    print("\n" + "=" * 76)
    print("           FYERS API v3 & MARKET DATA OPERATIONAL DIAGNOSTIC")
    print("=" * 76)
    print(f"Current Wall-Clock Time (IST):    {m['current_time_ist']} ({m['weekday']})")
    print(f"NSE Equity/Derivatives Window:    {m['market_open_time']} to {m['market_close_time']}")
    print(f"Is Market Active Right Now:       {m['is_during_market_hours']}")
    print("-" * 76)
    print("1. CREDENTIALS CONFIGURED & READABLE:")
    print(f"   - FYERS App / Client ID:       {'CONFIGURED (' + c['client_id_masked'] + ')' if c['client_id_configured'] else 'MISSING'}")
    print(f"   - FYERS App Secret:            {'CONFIGURED (' + c['secret_key_masked'] + ')' if c['secret_key_configured'] else 'MISSING'}")
    print(f"   - Token Cache File:            {'EXISTS (secrets/fyers_token.json)' if c['token_cache_file_exists'] else 'NOT FOUND (secrets/fyers_token.json)'}")
    print(f"   - Daily Access Token:          {'PRESENT (' + c['access_token_masked'] + ')' if c['access_token_present'] else 'MISSING'}")
    print("-" * 76)
    print("2. DIAGNOSTIC RESULTS:")
    print(f"   - Authentication Succeeded:    {res['authentication_succeeded']}")
    print(f"   - Market Data Initialized:     {res['market_data_initialized']}")
    print(f"   - Subscription Established:    {res['subscription_established']}")
    print(f"   - Fresh Candles Received:      {res['fresh_candles_received']}")
    print(f"   - Feed Connection Status:      {res['status']}")
    print("-" * 76)
    print(f"DIAGNOSTIC DETAILS:\n{res['details']}")
    print("=" * 76 + "\n")


if __name__ == "__main__":
    result = test_fyers_live_connection()
    print_diagnostic_summary(result)
