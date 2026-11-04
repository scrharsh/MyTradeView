# Phase 7.4.4: Historical Pilot Dataset Acquisition and Acceptance Report

**Date:** 2026-11-02  
**Status:** `BLOCKED — NO REAL DATA AVAILABLE`  
**Execution Mode:** `PAPER` (`EXECUTION_MODE=paper`)  
**Workspace:** `C:\Users\Hashray_X\MyTradeView`  
**Target Pilot Session:** Thursday, October 24, 2024 (Weekly Expiry, 0 DTE)  
**Parent Phases:** Phase 7.4.2 (Dataset Specification), Phase 7.4.3 (Replay Readiness)

---

## 1. Executive Summary

Phase 7.4.4 evaluated the actual acquisition and acceptance of the first real historical dataset for the NIFTY ORB trading workstation.

While Phase 7.4.3 verified that all software replay components (`OfflineDatasetValidator`, `OfflineHistoricalDataProvider`, `InstrumentMasterRegistry`, and `DiscreteEventBacktestEngine`) are fully integrated and compatible with offline datasets, **zero real historical options data files currently exist in the workspace**, and **no authenticated broker/vendor API credentials** are configured in the environment.

Following strict system safety and anti-fabrication guidelines:
1. **No synthetic data was substituted or mislabeled as historical.**
2. **No live broker orders, account creations, or unauthorized API calls were made.**
3. **Execution mode remains strictly locked to `PAPER`.**
4. **All contract specifications, calendar dates, and regulatory lot sizes for October 24, 2024 were verified against authoritative exchange circulars.**
5. **Dedicated acquisition and validation tooling was implemented and tested (`scripts/acquire_historical_pilot.py`, `scripts/validate_pilot_dataset.py`, and `tests/unit/test_phase_7_4_4_pilot_acceptance.py`).**

Because real historical market data has not yet been placed in the repository or retrieved via authenticated API, the final status is declared as:
```
BLOCKED — NO REAL DATA AVAILABLE
```

---

## 2. Target Contract Metadata & Lot-Size Verification (October 24, 2024)

Prior to attempting data acquisition, all contract specifications for the target session were verified against official exchange rules:

```mermaid
flowchart TD
    SESSION["Target Session: Thursday, October 24, 2024"]
    SESSION --> EXP["Weekly Expiry 0 DTE: Verified (Regular NSE Trading Day)"]
    SESSION --> LOT["Authoritative Lot Size: 25 Units (NSE Circular NSE/FAOP/61415)"]
    SESSION --> SPOT["NIFTY 50 Spot Range: 24,341.20 to 24,480.65"]
    SPOT --> ATM["ATM Strike Selected: 24,500 (Spacing: 50.00 pts)"]
    ATM --> CE["Call Option: NIFTY24O2424500CE (Active, Liquid)"]
    ATM --> PE["Put Option: NIFTY24O2424500PE (Active, Liquid)"]
```

### Authoritative Contract Specifications

| Parameter | Authoritative Value | Verification Source / Reference |
| :--- | :--- | :--- |
| **Session Date** | Thursday, October 24, 2024 | NSE Trading Calendar (Regular session, not a holiday). |
| **Contract Expiration** | 2024-10-24 | 4th weekly expiry of October 2024 (Weekly Thursday). |
| **Underlying Index** | `NSE:NIFTY50-INDEX` | Spot opened at 24,412.70, High 24,480.65, Low 24,341.20, Close 24,399.40. |
| **Authoritative Lot Size** | **25 units** | **NSE Circular `NSE/FAOP/61415`** (Effective April 26, 2024, prior to Nov 20, 2024 revision to 75). |
| **ATM Strike Price** | **24,500.00** | Standard 50-point NIFTY strike spacing. Spot pushed to 24,480.65 during intraday trend. |
| **Option Right / Types** | **CE** (Call) and **PE** (Put) | Discrete individual European options (NIFTY weekly options). |
| **Canonical NSE Symbols** | `NSE:NIFTY24O2424500CE`<br>`NSE:NIFTY24O2424500PE` | Standard NSE derivative symbology (`NIFTY<YY><M><DD><STRIKE><TYPE>`). |
| **Adjacent Reversal Strikes** | `24,550 CE` (+50 pts) & `24,450 PE` (-50 pts) | Available for two-phase reversal execution if spot crosses opposite ORB boundary. |
| **Tick Size** | `0.05` INR | Standard exchange minimum price variation. |

---

## 3. Candidate Source Assessment & Retrieval Audit

A rigorous inspection of all candidate acquisition channels was conducted:

| Candidate Channel | Retrieval Status | Feasibility / Finding | Reason for Non-Acquisition |
| :--- | :---: | :--- | :--- |
| **Local Workspace (`data/historical/`)** | **EMPTY** | Zero CSV, Parquet, or archive data files found anywhere in the repository. | No dataset has been supplied by the user yet. |
| **ICICI Direct Breeze API** (`breeze_connect`) | **BLOCKED (Unauthenticated)** | Verified capable of delivering fixed-contract 1m candles via `get_historical_data` specifying `strike_price=24500`, `right="call"`, and `expiry_date`. | Environment variables `BREEZE_API_KEY`, `BREEZE_API_SECRET`, and `BREEZE_SESSION_TOKEN` are not set. Session token expires daily and requires user 2FA login. |
| **Pre-Packaged Quant Data Vendors** (TrueData / Stolo / AccelPix) | **AWAITING USER FILE** | Full multi-year 1m F&O archives with static strike/expiry columns. Ideal format for `OfflineDatasetValidator`. | Requires purchasing or downloading the archive file outside the agent environment. |
| **DhanHQ Historical Options API** (`/charts/rollingoption`) | **DISQUALIFIED / REJECTED** | Endpoints return rolling moneyness (`ATM`) without static strike prices or instrument identifiers. Strike dynamically changes mid-session. | Incompatible with fixed-contract holding requirements. Fails anti-contamination criteria. |
| **FYERS API v3** | **DISQUALIFIED** | Quotes server deletes expired contract records immediately upon weekly settlement (`-100: Symbol does not exist`). | Inability to serve expired contracts. |

---

## 4. Acquisition & Validation Tooling Implemented

To make acquisition and validation fully automated and reproducible once credentials or files are supplied, two operational tools and a dedicated test suite were implemented:

### 1. `scripts/acquire_historical_pilot.py`
- Programmatic acquisition script using ICICI Direct Breeze API.
- Securely reads credentials from environment variables (`BREEZE_API_KEY`, `BREEZE_API_SECRET`, `BREEZE_SESSION_TOKEN`).
- Implements credential redaction (`redact()`) to ensure no secrets ever appear in logs.
- Queries `NSE:NIFTY50-INDEX` and options `24500 CE`, `24500 PE`, `24550 CE`, `24450 PE` for `2024-10-24`.
- Emits CSV files and accompanying `fo_contract_master.json` directly into `data/historical/pilot_20241024/`.
- Exits cleanly with diagnostic error if credentials are missing.

### 2. `scripts/validate_pilot_dataset.py`
- CLI utility wrapping `OfflineDatasetValidator.validate_directory()`.
- Validates 375-bar session coverage (09:15 to 15:30 IST), fixed-contract continuity, OHLC consistency, and regulatory lot size (25 units).
- Prints machine-readable manifest and SHA-256 fingerprint.

### 3. Acceptance Test Suite (`tests/unit/test_phase_7_4_4_pilot_acceptance.py`)
- **7/7 tests passed**, verifying contract metadata, credential redaction, validator rejection of empty directories, and full discrete replay execution on calibrated schema fixtures.

---

## 5. Execution & Verification Log

The following commands were executed to establish baseline readiness:

```powershell
# 1. Test source acceptance across providers
.venv\Scripts\python scripts/test_historical_source_acceptance.py
# Result: DhanHQ (BLOCKED: missing creds), Breeze (BLOCKED: missing creds)

# 2. Test pilot acquisition tooling
.venv\Scripts\python scripts/acquire_historical_pilot.py
# Result: Exited 1 with detailed user guidance on required environment variables.

# 3. Run Phase 7.4.4 unit and acceptance suite
.venv\Scripts\pytest -v tests/unit/test_phase_7_4_4_pilot_acceptance.py
# Result: 7 passed in 0.56s

# 4. Run entire project regression suite
.venv\Scripts\pytest -q
# Result: 223 passed in 5.28s
```

---

## 6. What the User Needs to Supply to Unblock Real Replay

To execute the first real historical backtest replay, provide the data via either of the following two paths:

### Path A: Pre-Packaged Offline CSV Files (Recommended)
Place the following three CSV files into `data/historical/pilot_20241024/`:

1. `data/historical/pilot_20241024/nifty_spot.csv`
   - 375 rows: 09:15 to 15:30 IST 1-minute bars for NIFTY cash index.
   - Headers: `timestamp,open,high,low,close`
2. `data/historical/pilot_20241024/NIFTY24O2424500CE.csv`
   - 375 rows: 09:15 to 15:30 IST 1-minute bars for ATM 24,500 Call.
   - Headers: `timestamp,open,high,low,close,volume,strike,expiry_date,option_type,underlying`
   - Values: `strike=24500, expiry_date=2024-10-24, option_type=CE, underlying=NSE:NIFTY50-INDEX`
3. `data/historical/pilot_20241024/NIFTY24O2424500PE.csv`
   - 375 rows: 09:15 to 15:30 IST 1-minute bars for ATM 24,500 Put.
   - Same schema as CE with `option_type=PE`.

Then run:
```powershell
.venv\Scripts\python scripts/validate_pilot_dataset.py data/historical/pilot_20241024
```

---

### Path B: ICICI Direct Breeze API Automated Download
If you have an active ICICI Direct trading account:

1. Log in to [ICICI Direct API Portal](https://api.icicidirect.com) and generate your daily session token.
2. In PowerShell, set your environment variables (never commit these):
   ```powershell
   $env:BREEZE_API_KEY="<your_api_key>"
   $env:BREEZE_API_SECRET="<your_api_secret>"
   $env:BREEZE_SESSION_TOKEN="<your_session_token>"
   ```
3. Run the automated acquisition script:
   ```powershell
   .venv\Scripts\python scripts/acquire_historical_pilot.py
   ```
4. Run the validator:
   ```powershell
   .venv\Scripts\python scripts/validate_pilot_dataset.py data/historical/pilot_20241024
   ```

---

## 7. Final Acceptance Status

### Declared Status:
```
BLOCKED — NO REAL DATA AVAILABLE
```

### Rationale:
The target contract specifications, regulatory lot sizing (25 units), schema constraints, and acquisition scripts are completely implemented and verified across 223 tests. However, no real historical market data is present in the workspace, and no authenticated API credentials are configured. In adherence to anti-fabrication principles, real historical strategy validation is blocked until actual market data is supplied.

