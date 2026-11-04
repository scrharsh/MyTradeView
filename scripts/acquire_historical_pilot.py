"""Automated, secure historical pilot dataset acquisition tool for MyTradeView.

Acquires the October 24, 2024 NIFTY weekly options pilot dataset using ICICI Direct Breeze API.

Target Assets:
- Underlying Spot: NSE:NIFTY50-INDEX (375 1-minute bars: 09:15 to 15:30 IST)
- ATM Call Option: NIFTY 24500 CE (Expiry: 2024-10-24, Lot Size: 25)
- ATM Put Option:  NIFTY 24500 PE (Expiry: 2024-10-24, Lot Size: 25)
- OTM Reversal Call: NIFTY 24550 CE
- OTM Reversal Put:  NIFTY 24450 PE

SECURITY RULES:
- Never hardcode or commit API credentials or session tokens.
- Reads BREEZE_API_KEY, BREEZE_API_SECRET, BREEZE_SESSION_TOKEN from environment.
- Redacts all secrets from log output.
"""

import json
import logging
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("PilotAcquisition")


def redact(val: Optional[str]) -> str:
    if not val:
        return "<NOT_SET>"
    if len(val) <= 6:
        return "***"
    return f"{val[:3]}...{val[-3:]}"


def acquire_breeze_pilot(
    output_dir: Path,
    session_date: str = "2024-10-24",
    atm_strike: int = 24500,
) -> bool:
    """Fetch 1-minute historical candles from ICICI Breeze API and save to output_dir."""
    api_key = os.environ.get("BREEZE_API_KEY")
    api_secret = os.environ.get("BREEZE_API_SECRET")
    session_token = os.environ.get("BREEZE_SESSION_TOKEN")

    logger.info("=" * 70)
    logger.info("ICICI DIRECT BREEZE HISTORICAL PILOT ACQUISITION")
    logger.info("=" * 70)
    logger.info("Target Session: %s", session_date)
    logger.info("ATM Strike:     %d", atm_strike)
    logger.info("BREEZE_API_KEY:       %s", redact(api_key))
    logger.info("BREEZE_API_SECRET:    %s", redact(api_secret))
    logger.info("BREEZE_SESSION_TOKEN: %s", redact(session_token))

    if not api_key or not api_secret or not session_token:
        logger.error(
            "AUTHENTICATION BLOCKED: BREEZE_API_KEY, BREEZE_API_SECRET, or BREEZE_SESSION_TOKEN "
            "is missing from the environment."
        )
        logger.error("To acquire data, generate a session token at https://api.icicidirect.com and set:")
        logger.error("  $env:BREEZE_API_KEY=\"<your_key>\"")
        logger.error("  $env:BREEZE_API_SECRET=\"<your_secret>\"")
        logger.error("  $env:BREEZE_SESSION_TOKEN=\"<your_daily_session_token>\"")
        return False

    try:
        from breeze_connect import BreezeConnect
    except ImportError:
        logger.error("breeze-connect library is not installed. Run: pip install breeze-connect")
        return False

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        breeze = BreezeConnect(api_key=api_key)
        breeze.generate_session(api_secret=api_secret, session_token=session_token)
        logger.info("Breeze session generated successfully.")

        start_dt = f"{session_date}T09:15:00.000Z"
        end_dt = f"{session_date}T15:30:00.000Z"
        expiry_dt = f"{session_date}T07:00:00.000Z"

        # 1. Acquire Spot Index Data
        logger.info("Fetching NIFTY Spot index candles...")
        spot_resp = breeze.get_historical_data(
            interval="1minute",
            from_date=start_dt,
            to_date=end_dt,
            stock_code="NIFTY",
            exchange_code="NSE",
            product_type="cash",
        )

        if not spot_resp or "Success" not in spot_resp or not spot_resp["Success"]:
            logger.error("Failed to fetch NIFTY spot data: %s", spot_resp.get("Error", "Empty response"))
            return False

        spot_records = spot_resp["Success"]
        logger.info("Received %d bars for NIFTY spot.", len(spot_records))
        spot_rows = []
        for r in spot_records:
            spot_rows.append({
                "timestamp": r["datetime"],
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": int(r.get("volume", 0) or 0),
            })
        spot_df = pd.DataFrame(spot_rows)
        spot_path = output_dir / "nifty_spot.csv"
        spot_df.to_csv(spot_path, index=False)
        logger.info("Saved spot data to %s", spot_path)

        # 2. Acquire Target Option Contracts
        contracts_to_fetch = [
            (atm_strike, "call", "CE"),
            (atm_strike, "put", "PE"),
            (atm_strike + 50, "call", "CE"),
            (atm_strike - 50, "put", "PE"),
        ]

        master_contracts = []
        for strike, right, opt_type in contracts_to_fetch:
            sym_name = f"NIFTY_{session_date.replace('-', '')}_{strike}_{opt_type}"
            logger.info("Fetching option candles for %s (%s)...", sym_name, right)
            opt_resp = breeze.get_historical_data(
                interval="1minute",
                from_date=start_dt,
                to_date=end_dt,
                stock_code="NIFTY",
                exchange_code="NFO",
                product_type="options",
                expiry_date=expiry_dt,
                right=right,
                strike_price=strike,
            )

            if not opt_resp or "Success" not in opt_resp or not opt_resp["Success"]:
                logger.warning("Failed to fetch %s: %s", sym_name, opt_resp.get("Error", "Empty response"))
                continue

            records = opt_resp["Success"]
            logger.info("Received %d bars for %s.", len(records), sym_name)
            opt_rows = []
            for r in records:
                opt_rows.append({
                    "timestamp": r["datetime"],
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": int(r.get("volume", 0) or 0),
                    "strike": float(strike),
                    "expiry_date": session_date,
                    "option_type": opt_type,
                    "underlying": "NSE:NIFTY50-INDEX",
                })
            opt_df = pd.DataFrame(opt_rows)
            opt_path = output_dir / f"{sym_name}.csv"
            opt_df.to_csv(opt_path, index=False)
            logger.info("Saved %s to %s", sym_name, opt_path)

            master_contracts.append({
                "symbol": f"NSE:{sym_name}",
                "strike": float(strike),
                "option_type": opt_type,
                "expiry": session_date,
                "lot_size": 25,
                "file_path": f"{sym_name}.csv",
            })

        # 3. Create fo_contract_master.json
        master_meta = {
            "session_date": session_date,
            "underlying": "NSE:NIFTY50-INDEX",
            "resolved_expiry": session_date,
            "lot_size": 25,
            "tick_size": 0.05,
            "available_contracts": master_contracts,
        }
        with open(output_dir / "fo_contract_master.json", "w", encoding="utf-8") as f:
            json.dump(master_meta, f, indent=2)
        logger.info("Generated fo_contract_master.json with %d contracts.", len(master_contracts))

        return True

    except Exception as e:
        logger.error("Exception during Breeze pilot acquisition: %s", e, exc_info=True)
        return False


def main() -> None:
    output_directory = Path("data/historical/pilot_20241024")
    success = acquire_breeze_pilot(output_directory)
    if not success:
        sys.exit(1)
    logger.info("Acquisition completed successfully.")


if __name__ == "__main__":
    main()

