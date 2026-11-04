# Phase 7.4.3: Historical Data Acquisition and Replay Readiness Report

**Date:** 2026-11-02  
**Status:** `REPLAY SOFTWARE VERIFIED — REAL DATA STILL REQUIRED`  
**Execution Mode:** `PAPER` (`EXECUTION_MODE=paper`)  
**Workspace:** `C:\Users\Hashray_X\MyTradeView`  
**Parent Phases:** Phase 7.4.1 (Data Source Acceptance), Phase 7.4.2 (Dataset Specification & Offline Validator)

---

## 1. Executive Summary & Audit Baseline

Phase 7.4.3 establishes the complete end-to-end operational readiness of the historical replay pipeline in MyTradeView, resolving four critical architectural integration defects that previously prevented offline historical datasets from driving backtests through the strategy and execution engines.

Before this phase, while `OfflineDatasetValidator` and `OfflineHistoricalDataProvider` existed in isolation, validated offline contracts could not register into `InstrumentMasterRegistry`. Consequently, `ContractResolver` was unable to resolve historical option contracts without falling back to mock configurations. Furthermore, `WorkstationService` hardcoded `is_synthetic=True` on backtest run summaries, and `BacktestRun` lacked persistent dataset provenance and cryptographic SHA-256 fingerprint tracking.

All four integration defects have been corrected, verified against controlled synthetic test fixtures, and validated across the entire test suite:
- **Unit and Integration Tests:** 216/216 passing tests (`tests/unit/test_phase_7_4_3_replay_readiness.py` adds 9 comprehensive integration tests).
- **Frontend Production Build:** Next.js 14 compiles and passes typechecking cleanly (12/12 static routes prerendered).
- **Execution Safety:** System remains strictly locked to `PAPER` execution mode. Zero broker orders or live API calls were made.
- **Data Integrity:** No synthetic data has been relabeled as historical. Because zero authentic historical options files are currently present in `data/historical/`, the final status is declared as `REPLAY SOFTWARE VERIFIED — REAL DATA STILL REQUIRED`.

---

## 2. Replay Pipeline Architectural Trace & Resolved Integration Defects

The complete data pipeline was traced from raw disk files to persisted `BacktestRun` database entities:

```mermaid
flowchart TD
    A["Raw Historical Files (Spot + Option CSV/Parquet)"] --> B["OfflineDatasetValidator.validate_directory()"]
    B -->|Check 375 bars, 09:15-15:30 IST, OHLCV, SHA-256| C["DatasetManifest (fingerprint, option_symbols)"]
    C --> D["OfflineHistoricalDataProvider.load_dataset_directory()"]
    D -->|populate_registry()| E["InstrumentMasterRegistry.register_option_contract()"]
    E --> F["ContractResolver.resolve_atm_contract()"]
    D -->|Underlying & Option Candle Maps| G["DiscreteEventBacktestEngine.run()"]
    F -->|Authoritative OptionContract| G
    G -->|Lookahead-Free Execution at 09:47:00 Open| H["BacktestRun (trades, metrics, fingerprint, provenance)"]
    H --> I["WorkstationService._format_backtest_summary()"]
    I -->|Dynamic is_synthetic flag| J["Frontend Workstation UI / API Response"]
```

### Resolved Integration Defects

| Defect # | Component | Root Cause | Resolution |
| :--- | :--- | :--- | :--- |
| **Defect 1** | `InstrumentMasterRegistry` | Master registry only ingested contracts from FYERS CSV dumps via `parse_csv()`. Validated offline contracts had no registration interface. | Implemented `register_option_contract(self, contract: OptionContract)` in [`src/data/instrument_master.py`](file:///C:/Users/Hashray_X/MyTradeView/src/data/instrument_master.py), enabling dynamic indexing of historical contracts. |
| **Defect 2** | `OfflineHistoricalDataProvider` | Loader cached candles but failed to populate the contract registry, causing `ContractResolver` to fail closed or raise `DataValidationError`. | Added `populate_registry(self, registry)` and wired optional `registry` parameter into `load_dataset_directory()` in [`src/data/offline_provider.py`](file:///C:/Users/Hashray_X/MyTradeView/src/data/offline_provider.py). |
| **Defect 3** | `WorkstationService` | Summary formatter hardcoded `is_synthetic=True` regardless of actual dataset provenance. | Updated line 1332 of [`src/api/service.py`](file:///C:/Users/Hashray_X/MyTradeView/src/api/service.py) to `is_synthetic=getattr(run, "is_synthetic", True)` to dynamically honor dataset metadata. |
| **Defect 4** | `BacktestRun` Domain Model | Backtest results lacked provenance tracking, making it impossible to audit which dataset fingerprint produced a backtest run. | Added `dataset_fingerprint`, `dataset_provenance`, and `is_synthetic` fields to `BacktestRun` in [`src/backtest/models.py`](file:///C:/Users/Hashray_X/MyTradeView/src/backtest/models.py) and engine run args. |

---

## 3. Fixed-Contract Continuity Enforcement & Lookahead-Free Timing

### Fixed-Contract Continuity (No Strike or Expiry Switching)

A major risk identified in Phase 7.4.1 was that certain vendor APIs (such as DhanHQ) provide "rolling" option data that recalculates ATM strikes dynamically throughout the session. In live and authentic historical trading, a trader buys a **specific, fixed strike and expiry** and holds that exact contract until exit.

The MyTradeView replay engine enforces fixed-contract preservation across the position lifecycle:
1. **Initial Breakout Trigger:** When the NIFTY spot breaks out above the Opening Range high at 09:46:00 IST close, `NiftyOrbStrategy` calls `ContractResolver.resolve_atm_contract()`.
2. **Contract Pinning:** The resolver selects the exact weekly 0 DTE ATM strike (e.g. `24500 CE`) and emits a `StrategySignal` specifying `target_contract`.
3. **Execution Lock:** In `DiscreteEventBacktestEngine._execute_pending_orders()`, the trade is created with `trade["symbol"]` and `trade["contract"]` permanently locked to the resolved contract.
4. **Mark-to-Market & Exit:** Subsequent candle iterations exclusively evaluate option candles matching `active_trade["symbol"]`. No strike rolls or expiry substitutions are permitted.

### Lookahead-Free Execution Timing

Execution strictly avoids lookahead bias:
- **Signal Generation:** Breakout confirmation occurs at **09:46:00 IST bar close**.
- **Order Queueing:** Order is marked `PENDING` at 09:46:00.
- **Execution Bar:** The order fills at **09:47:00 IST bar open** using the 09:47 option candle's open price.
- **Frictional Costs:** Fixed 0.5-point slippage is applied to entry and exit, plus statutory STT, exchange turnover fees, SEBI charges, stamp duty, and GST.
- **Data Gap Handling:** If the option candle for 09:47:00 IST is missing from the dataset, the order **cannot execute**. It increments `diagnostics.missing_data_events` and aborts rather than interpolating synthetic prices.

---

## 4. Authoritative Regulatory Lot-Size History (NSE Circulars)

The NIFTY 50 index derivative lot size has undergone multiple regulatory revisions by the National Stock Exchange of India (NSE). Historical backtests must dynamically apply the lot size that was legally effective on the trading date:

| Effective Period | Lot Size | Authoritative NSE Circular Reference | Description |
| :--- | :---: | :--- | :--- |
| **Prior to April 26, 2024** | **50** | `NSE/FAOP/59914` & previous periodic reviews | Longstanding historical contract lot size for NIFTY derivatives. |
| **April 26, 2024 – Nov 19, 2024** | **25** | `NSE/FAOP/61415` (Issued April 04, 2024) | Lot size halved from 50 to 25. First weekly expiry trading at 25 was May 02, 2024. |
| **November 20, 2024 – Dec 29, 2025** | **75** | `NSE/FAOP/64625` (Issued Oct 18, 2024) | Re-standardization of index derivative contracts; lot size increased to 75. |
| **December 30, 2025 – Present** | **65** | Periodic circular review | Adjustment to 65 units per contract. |

In `OfflineDatasetValidator.get_authoritative_lot_size(session_date)` and `InstrumentMasterRegistry`, lot sizes are evaluated dynamically against these cutoff dates. Test 5 verifies that a backtest run on an October 2023 session accurately trades 50 units per lot, whereas an October 2024 session trades 25 units per lot, scaling gross P&L and brokerage correctly.

---

## 5. Controlled Synthetic Replay Test Results

The new test suite [`tests/unit/test_phase_7_4_3_replay_readiness.py`](file:///C:/Users/Hashray_X/MyTradeView/tests/unit/test_phase_7_4_3_replay_readiness.py) executes 9 automated verification tests:

| Test Case | Description | Result |
| :--- | :--- | :---: |
| `test_end_to_end_replay_path_with_fixture` | Complete path: Directory -> Validator -> Provider -> Registry -> Resolver -> Engine -> BacktestRun. | **PASSED** |
| `test_contract_selection_preserves_static_strike_and_expiry_throughout_position` | Confirms trade strike/expiry never changes across trade duration. | **PASSED** |
| `test_missing_option_candles_blocks_execution_fill` | Confirms missing 09:47 option bar blocks fill and logs missing data event. | **PASSED** |
| `test_missing_contract_in_registry_fails_closed` | Confirms empty registry raises `DataValidationError` without guessing. | **PASSED** |
| `test_historical_lot_size_flows_into_trade_pnl` | Confirms 2023 session trades 50 qty and scales P&L vs 25 qty in 2024. | **PASSED** |
| `test_dataset_fingerprint_changes_on_data_mutation` | Confirms modifying a single price changes SHA-256 fingerprint deterministically. | **PASSED** |
| `test_api_service_preserves_dynamic_synthetic_flag` | Confirms API service passes `is_synthetic=False` for historical runs. | **PASSED** |
| `test_lookahead_free_next_bar_open_fill_timing` | Confirms breakout at 09:46 close fills at exactly 09:47:00 IST bar open. | **PASSED** |
| `test_stockmock_benchmark_calibration_with_matched_trade` | Confirms StockMock comparator verifies exact trade parity when provided calibrated trade log. | **PASSED** |

**Full Suite Regression Check:** 216 tests passed in 4.52 seconds with zero errors.

---

## 6. Historical Data Acquisition Plan & Pilot Dataset Specification

The comprehensive acquisition plan has been published to [`docs/HISTORICAL_DATA_ACQUISITION_PLAN.md`](file:///C:/Users/Hashray_X/MyTradeView/docs/HISTORICAL_DATA_ACQUISITION_PLAN.md).

### Target Pilot Dataset: Thursday, October 24, 2024 (0 DTE Weekly Expiry)

To validate the engine against real market action with minimal vendor friction, the pilot targets a single complete weekly expiry session:

- **Target Session:** Thursday, October 24, 2024
- **Contract Type:** NIFTY Weekly Options (Expiry: 2024-10-24, 0 DTE)
- **Lot Size:** 25 units
- **Required Files:**
  1. `data/historical/20241024/nifty_spot.csv`: 375 consecutive 1-minute bars (09:15 to 15:30 IST)
  2. `data/historical/20241024/NIFTY_20241024_24500_CE.csv`: 375 consecutive 1-minute bars for ATM CE
  3. `data/historical/20241024/NIFTY_20241024_24500_PE.csv`: 375 consecutive 1-minute bars for ATM PE

### Minimum Acceptance Thresholds
- **Bar Count:** Exactly 375 1-minute bars per file.
- **Timestamp Coverage:** Monotonic, gapless 09:15:00 to 15:29:00 IST start times.
- **Price Validity:** High >= Max(Open, Close) and Low <= Min(Open, Close). Open, High, Low, Close > 0.
- **Volume:** Spot volume = 0, Option volume > 0.

---

## 7. Provider Assessment & Rejection Rationales

| Data Source | Capability Status | Backtest Suitability | Rationale / Recommendation |
| :--- | :--- | :--- | :--- |
| **ICICI Direct Breeze API** | Documented support for fixed-contract historical options candles (`get_historical_data_v2`). | **Conditionally Accepted** | Valid candidate for fixed contracts, but requires active ICICI Direct account and daily session token to fetch candles. Suitable once credentials are supplied. |
| **DhanHQ Historical Options API** | Rolling delta/moneyness candles only (`/v1/charts/historical/options`). | **REJECTED** | Fails fixed-contract continuity test. Dhan recalculates strikes dynamically as spot moves, resulting in synthetic rolled series rather than discrete contract holding. |
| **Offline Quant Data Vendors** (GlobalDataFeeds, TruData, AccelPix) | Pre-packaged 1-minute CSV/Parquet archives for expired NSE option contracts. | **RECOMMENDED** | Fully compatible with `OfflineDatasetValidator` and `load_dataset_directory()`. Provides pure raw market data with zero API session friction. |

---

## 8. Current Workspace Data Inventory & Verification

An exhaustive scan of `C:\Users\Hashray_X\MyTradeView` confirms:
- **No Real Historical Options Files:** The directory `data/historical/` is currently empty of real CSV or Parquet files.
- **Synthetic Test Isolation:** All synthetic fixtures generated during testing are located strictly in temporary test directories (`tmp_path`) or generated on-the-fly in memory with explicit `is_synthetic=True` flags.
- **Zero Hallucination Guarantee:** The system does not treat synthetic data as authentic market history.

---

## 9. Final Acceptance Status

### Declared Status:
```
REPLAY SOFTWARE VERIFIED — REAL DATA STILL REQUIRED
```

### Rationale:
The software architecture for historical replay is fully verified, integrated, and validated across 216 tests. Fixed-contract continuity, lookahead-free next-bar open fills, regulatory lot-size schedules, and dataset provenance hashing operate correctly. However, because no authentic historical options dataset has yet been ingested into `data/historical/`, claims of historical strategy performance or StockMock parity cannot be made until authentic market files are acquired.

