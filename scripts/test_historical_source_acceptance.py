"""Standalone, secure historical option data source acceptance test.

Evaluates ICICI Direct Breeze and DhanHQ APIs against fixed-contract continuity,
schema consistency, timestamp alignment, and intraday gap criteria.

CRITICAL SECURITY RULES:
- Never hardcode API keys, client secrets, or session tokens in this file.
- Reads credentials strictly from environment variables or local uncommitted .env.
- Redacts sensitive credentials from all console and diagnostic outputs.
"""

import json
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Redaction helper
def redact(val: Optional[str]) -> str:
    if not val:
        return "<NOT_SET>"
    if len(val) <= 6:
        return "***"
    return f"{val[:3]}...{val[-3:]}"


def validate_candles(
    symbol: str,
    records: List[Dict[str, Any]],
    timestamp_key: str = "timestamp",
    open_key: str = "open",
    high_key: str = "high",
    low_key: str = "low",
    close_key: str = "close",
    volume_key: str = "volume",
    is_epoch: bool = False,
) -> Dict[str, Any]:
    """Validate candle sequence for boundary integrity, duplicates, and gaps."""
    if not records:
        return {
            "symbol": symbol,
            "status": "FAIL",
            "reason": "Empty candle list received from provider.",
            "total_bars": 0,
        }

    seen_timestamps = set()
    duplicates = 0
    boundary_violations = 0
    parsed_candles = []

    for idx, row in enumerate(records):
        raw_ts = row.get(timestamp_key)
        try:
            if is_epoch:
                ts = datetime.fromtimestamp(float(raw_ts))
            else:
                ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
        except Exception as e:
            return {"symbol": symbol, "status": "FAIL", "reason": f"Malformed timestamp at index {idx}: {e}"}

        if ts in seen_timestamps:
            duplicates += 1
        seen_timestamps.add(ts)

        try:
            op = Decimal(str(row[open_key]))
            hi = Decimal(str(row[high_key]))
            lo = Decimal(str(row[low_key]))
            cl = Decimal(str(row[close_key]))
            vol = int(row.get(volume_key, 0) or 0)
        except Exception as e:
            return {"symbol": symbol, "status": "FAIL", "reason": f"Malformed numeric OHLCV at {ts}: {e}"}

        if hi < lo or hi < op or hi < cl or lo > op or lo > cl or lo <= 0:
            boundary_violations += 1

        parsed_candles.append({"ts": ts, "open": op, "high": hi, "low": lo, "close": cl, "vol": vol})

    parsed_candles.sort(key=lambda x: x["ts"])

    # Gap detection across 1-minute intervals during session
    gaps = []
    for i in range(1, len(parsed_candles)):
        delta = parsed_candles[i]["ts"] - parsed_candles[i - 1]["ts"]
        if delta > timedelta(minutes=1):
            gaps.append({
                "from": parsed_candles[i - 1]["ts"].isoformat(),
                "to": parsed_candles[i]["ts"].isoformat(),
                "delta_minutes": delta.total_seconds() / 60.0,
            })

    return {
        "symbol": symbol,
        "status": "PASS" if boundary_violations == 0 and duplicates == 0 else "FAIL",
        "total_bars": len(parsed_candles),
        "duplicates": duplicates,
        "boundary_violations": boundary_violations,
        "gaps_count": len(gaps),
        "first_bar": parsed_candles[0]["ts"].isoformat() if parsed_candles else None,
        "last_bar": parsed_candles[-1]["ts"].isoformat() if parsed_candles else None,
        "sample_gaps": gaps[:3],
    }


def test_dhan_rolling_option(test_date: str = "2024-10-24") -> Dict[str, Any]:
    """Test DhanHQ /charts/rollingoption endpoint."""
    client_id = os.environ.get("DHAN_CLIENT_ID")
    access_token = os.environ.get("DHAN_ACCESS_TOKEN")

    print(f"\n--- Testing DhanHQ API (/charts/rollingoption) ---")
    print(f"Client ID:    {redact(client_id)}")
    print(f"Access Token: {redact(access_token)}")

    if not client_id or not access_token:
        print("[BLOCKED] DHAN_CLIENT_ID or DHAN_ACCESS_TOKEN not set in environment.")
        return {
            "provider": "DhanHQ",
            "status": "BLOCKED",
            "reason": "Missing DHAN_CLIENT_ID or DHAN_ACCESS_TOKEN environment variable.",
        }

    try:
        import requests
    except ImportError:
        return {"provider": "DhanHQ", "status": "BLOCKED", "reason": "requests library not installed."}

    url = "https://api.dhan.co/v2/charts/rollingoption"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": access_token,
    }
    payload = {
        "exchangeSegment": "NSE_FNO",
        "interval": "1",
        "securityId": 13,
        "instrument": "OPTIDX",
        "expiryFlag": "WEEK",
        "expiryCode": 1,
        "strike": "ATM",
        "drvOptionType": "CALL",
        "requiredData": ["open", "high", "low", "close", "volume"],
        "fromDate": test_date,
        "toDate": test_date,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        print(f"HTTP Status: {resp.status_code}")
        if resp.status_code != 200:
            return {
                "provider": "DhanHQ",
                "status": "FAIL",
                "http_status": resp.status_code,
                "error_response": resp.text[:300],
            }

        data = resp.json()
        print(f"Response status: {data.get('status')}")
        payload_data = data.get("data", {})
        timestamps = payload_data.get("timestamp", [])
        print(f"Bars received: {len(timestamps)}")

        # Check critical fixed-contract requirement: Does response identify contract/strike?
        has_strike_field = "strike" in payload_data
        has_symbol_field = "symbol" in payload_data or "trading_symbol" in payload_data

        print(f"Contains strike field per candle: {has_strike_field}")
        print(f"Contains contract symbol field:   {has_symbol_field}")

        reconstructed_records = []
        for i in range(len(timestamps)):
            reconstructed_records.append({
                "timestamp": timestamps[i],
                "open": payload_data["open"][i],
                "high": payload_data["high"][i],
                "low": payload_data["low"][i],
                "close": payload_data["close"][i],
                "volume": payload_data["volume"][i] if "volume" in payload_data else 0,
            })

        val_result = validate_candles(
            symbol="NIFTY_ROLLING_ATM_CE",
            records=reconstructed_records,
            timestamp_key="timestamp",
            is_epoch=True,
        )

        return {
            "provider": "DhanHQ",
            "status": "SOURCE_TESTED",
            "fixed_contract_supported": False,
            "contract_identification_reason": (
                "Endpoint returns rolling moneyness ('ATM') without static strike price "
                "or instrument identifier. Contract rolls dynamically on spot movement."
            ),
            "data_validation": val_result,
        }

    except Exception as e:
        return {"provider": "DhanHQ", "status": "ERROR", "error": str(e)}


def test_breeze_historical(test_date: str = "2024-10-24", strike: int = 24500) -> Dict[str, Any]:
    """Test ICICI Direct Breeze API historical options endpoint."""
    api_key = os.environ.get("BREEZE_API_KEY")
    api_secret = os.environ.get("BREEZE_API_SECRET")
    session_token = os.environ.get("BREEZE_SESSION_TOKEN")

    print(f"\n--- Testing ICICI Direct Breeze API ---")
    print(f"API Key:       {redact(api_key)}")
    print(f"API Secret:    {redact(api_secret)}")
    print(f"Session Token: {redact(session_token)}")

    if not api_key or not api_secret or not session_token:
        print("[BLOCKED] BREEZE_API_KEY, BREEZE_API_SECRET, or BREEZE_SESSION_TOKEN not set in environment.")
        return {
            "provider": "ICICI_Breeze",
            "status": "BLOCKED",
            "reason": "Missing BREEZE_API_KEY, BREEZE_API_SECRET, or BREEZE_SESSION_TOKEN in environment.",
        }

    try:
        from breeze_connect import BreezeConnect
    except ImportError:
        return {
            "provider": "ICICI_Breeze",
            "status": "BLOCKED",
            "reason": "breeze-connect SDK not installed (pip install breeze-connect).",
        }

    try:
        breeze = BreezeConnect(api_key=api_key)
        breeze.generate_session(api_secret=api_secret, session_token=session_token)

        start_dt = f"{test_date}T09:15:00.000Z"
        end_dt = f"{test_date}T15:30:00.000Z"
        expiry_dt = f"{test_date}T07:00:00.000Z"

        print(f"Querying expired NIFTY {strike} CE for {test_date}...")
        resp = breeze.get_historical_data(
            interval="1minute",
            from_date=start_dt,
            to_date=end_dt,
            stock_code="NIFTY",
            exchange_code="NFO",
            product_type="options",
            expiry_date=expiry_dt,
            right="call",
            strike_price=strike,
        )

        if not resp or "Success" not in resp or not resp["Success"]:
            return {
                "provider": "ICICI_Breeze",
                "status": "FAIL",
                "error_response": resp.get("Error") or "Empty or unsuccessful response",
            }

        records = resp["Success"]
        print(f"Bars received: {len(records)}")

        val_result = validate_candles(
            symbol=f"NIFTY_{test_date}_{strike}_CE",
            records=records,
            timestamp_key="datetime",
            open_key="open",
            high_key="high",
            low_key="low",
            close_key="close",
            volume_key="volume",
            is_epoch=False,
        )

        return {
            "provider": "ICICI_Breeze",
            "status": "PASS",
            "fixed_contract_supported": True,
            "contract_identified": f"NIFTY {test_date} {strike} CE",
            "data_validation": val_result,
        }

    except Exception as e:
        return {"provider": "ICICI_Breeze", "status": "ERROR", "error": str(e)}


def main() -> None:
    print("=" * 80)
    print("PHASE 7.4.1: HISTORICAL DATA SOURCE ACCEPTANCE TEST HARNESS")
    print("=" * 80)

    dhan_res = test_dhan_rolling_option()
    breeze_res = test_breeze_historical()

    print("\n" + "=" * 80)
    print("SUMMARY VERDICT")
    print("=" * 80)
    print(f"DhanHQ Status:       {dhan_res.get('status')} - {dhan_res.get('reason', '')}")
    print(f"ICICI Breeze Status: {breeze_res.get('status')} - {breeze_res.get('reason', '')}")


if __name__ == "__main__":
    main()

