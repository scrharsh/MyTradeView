# Phase 7.3: Backtest Metrics and Strategy Validation Report

**Project:** MyTradeView Algorithmic Trading Platform (`C:\Users\Hashray_X\MyTradeView`)  
**Phase Baseline Commit:** `c533a20` (Following Phase 7.2 Safety Verification)  
**Execution Mode:** Strictly Restricted to `PAPER` (Live execution disabled by fail-closed gatekeepers)  
**Audit Date:** November 2, 2026  
**Final Status:** **`METRICS VERIFIED — HISTORICAL STRATEGY VALIDATION BLOCKED`**

---

## Executive Summary

Phase 7.3 completed an end-to-end mathematical audit and implementation refinement of the backtest metrics engine, resolved the Sharpe ratio semantic discrepancy discovered in Phase 7.2, implemented an exhaustive 14-scenario deterministic test suite, audited simulated transaction costs and slippage models, inventoried market datasets, and validated the NIFTY ORB strategy rules.

### Key Milestones Achieved:
1. **Mathematical Semantics Defined:** Rigorously specified and documented definitions for all 11 backtest performance metrics, including currency units, zero-division handling, and net-of-costs accounting.
2. **Sharpe Ratio Resolution:** Resolved the trade-level ratio defect. Implemented a dual-metric architecture:
   - **`sharpe_ratio`:** True annualized portfolio Sharpe ratio based on daily equity returns ($\mu_d / \sigma_d \times \sqrt{252}$). Returns `null` with explicit diagnostic explanation (`sharpe_ratio_reason`) when daily observations are insufficient ($< 2$ trading days).
   - **`trade_pnl_mean_std_ratio`:** Retained the trade net P&L consistency metric under an honest, unambiguous name.
3. **Deterministic Test Suite:** All 14 hand-calculated test scenarios passed with exact Decimal precision. Total test suite expanded to **188 passing tests** (0 failures).
4. **Historical Data Feasibility Audit:** Verified that no authentic historical expired option contracts exist in the repository, and confirmed that FYERS API v3 does not provide historical intraday candles for expired options.
5. **Final Verdict:** The backtesting metrics engine is verified and mathematically sound, but historical strategy validation and StockMock parity remain **BLOCKED** pending acquisition of historical 1-minute option candle datasets.

---

## 1. Repository Baseline & Environment

- **Current Git Branch:** `master`
- **Baseline Commit:** `c533a20`
- **Execution Mode:** `ExecutionMode.PAPER` enforced across backend runtime, OMS, and pre-trade gatekeeper.
- **Python Environment:** Python 3.10.11 (`.venv\Scripts\python.exe`), Pytest 9.2.0
- **Frontend Environment:** Next.js 14.2.35, TypeScript 5.4, Node.js v22.14.0

### Reproducible Build & Verification Commands

| Command | Exit Code | Results | Status |
| :--- | :---: | :--- | :---: |
| `.venv\Scripts\pytest -v` | `0` | **188 passed, 0 failed, 1 warning** (2.24s) | **PASS** |
| `.venv\Scripts\pytest -v tests\unit\test_phase_7_3_metrics.py` | `0` | **15 passed, 0 failed** (0.41s) | **PASS** |
| `cd frontend; npx tsc --noEmit` | `0` | **0 type errors** | **PASS** |
| `cd frontend; npm run build` | `0` | **12/12 static pages prerendered cleanly** | **PASS** |

---

## 2. Backtest Metric Semantics & Specification

The quantitative performance metrics engine ([`src/backtest/metrics.py`](file:///C:/Users/Hashray_X/MyTradeView/src/backtest/metrics.py)) computes metrics strictly using Decimal arithmetic to prevent floating-point drift.

| Metric | Mathematical Definition | Input Observations | Unit | Missing Data / Zero Handling | Scope |
| :--- | :--- | :--- | :---: | :--- | :---: |
| **Gross P&L** | $\sum_{i=1}^N (P_{\text{exit}, i} - P_{\text{entry}, i}) \times Q_i$ | Trade roundtrips | INR (₹) | Returns `₹0.00` if $N=0$ | Trade |
| **Net P&L** | $\sum_{i=1}^N (\text{Gross P\&L}_i - \text{Costs}_i)$ | Trade roundtrips | INR (₹) | Returns `₹0.00` if $N=0$ | Trade |
| **Win Rate** | $(\frac{N_{\text{win}}}{N_{\text{total}}}) \times 100$ | Trades with $\text{Net P\&L} > 0$ | $\%$ | Returns `0.00%` if $N=0$ | Trade |
| **Profit Factor** | $\frac{\sum \text{Gross Wins}}{\|\sum \text{Gross Losses}\|}$ | Gross trade P&L | Ratio | If losses $= 0$: `999.99` if wins $> 0$, else `0.00` | Trade |
| **Expectancy** | $\frac{\text{Net P\&L}}{N_{\text{total}}}$ | Trade net P&L | INR (₹) | Returns `₹0.00` if $N=0$ | Trade |
| **Average Win** | $\frac{\sum \text{Net Wins}}{N_{\text{win}}}$ | Trades with $\text{Net P\&L} > 0$ | INR (₹) | Returns `₹0.00` if $N_{\text{win}}=0$ | Trade |
| **Average Loss** | $\frac{\|\sum \text{Net Losses}\|}{N_{\text{loss}}}$ | Trades with $\text{Net P\&L} < 0$ | INR (₹) | Returns `₹0.00` if $N_{\text{loss}}=0$ | Trade |
| **Max Drawdown (₹)** | $\max_{t} (\text{Peak Equity}_t - \text{Equity}_t)$ | Equity curve | INR (₹) | Returns `₹0.00` if equity monotonically increases | Portfolio |
| **Max Drawdown (%)** | $\max_{t} (\frac{\text{Peak}_t - \text{Equity}_t}{\text{Peak}_t}) \times 100$ | Equity curve | $\%$ | Returns `0.00%` if equity monotonically increases | Portfolio |
| **Daily P&L** | $\sum_{i \in \text{Day } d} \text{Net P\&L}_i$ | Daily trade exits | INR (₹) | Only active trading dates recorded | Period |
| **Monthly P&L** | $\sum_{i \in \text{Month } m} \text{Net P\&L}_i$ | Monthly trade exits | INR (₹) | Grouped by `YYYY-MM` | Period |
| **Sharpe Ratio (Ann.)** | $(\frac{\mu_{\text{daily\_ret}} - R_f}{\sigma_{\text{daily\_ret}}}) \times \sqrt{252}$ | Daily portfolio returns | Ratio | Returns `null` if days $< 2$ or $\sigma=0$ | Portfolio |
| **Trade PnL / Stdev** | $\frac{\mu_{\text{trade\_net\_pnl}}}{\sigma_{\text{trade\_net\_pnl}}}$ | Trade net P&L | Ratio | Returns `null` if $N \le 1$ or $\sigma=0$ | Trade |

---

## 3. Sharpe Ratio Resolution: Dual-Metric Architecture

In Phase 7.2, an audit finding noted that the backtest summary computed $\mu_{\text{pnl}} / \sigma_{\text{pnl}}$ on trade rupee P&L and displayed it as "SHARPE RATIO".

### Implementation Resolution (Combining Option A & Option B):
1. **Annualized Portfolio Sharpe Ratio (`sharpe_ratio`):**
   - **Return Series:** Daily portfolio returns $R_t = \frac{\text{Daily Net P\&L}_t}{\text{Starting Portfolio Equity}_t}$.
   - **Annualization:** Multiplied by $\sqrt{252}$.
   - **Risk-Free Rate:** Assumed $R_f = 0.0$ (explicitly documented).
   - **Fail-Safe Threshold:** If the simulation spans fewer than 2 distinct daily return periods, `sharpe_ratio` returns `null` with `sharpe_ratio_reason: "Insufficient daily return observations (minimum 2 trading days required; found N)"`.
   - **Zero Variance:** If daily returns are identical ($\sigma = 0$), returns `null` with `sharpe_ratio_reason: "Zero variance in daily portfolio returns"`.
2. **Trade Consistency Ratio (`trade_pnl_mean_std_ratio`):**
   - The trade-level net P&L consistency metric is preserved under an honest, descriptive name: `trade_pnl_mean_std_ratio`.
   - Returns `null` if trade count $\le 1$ or if $\sigma_{\text{pnl}} = 0$.
3. **Frontend Presentation:**
   - The UI displays **`SHARPE RATIO (ANN.)`** as `"N/A"` for single-session runs with an informative tooltip, preventing users from assuming zero risk.
   - A dedicated **`TRADE PnL / STDEV`** tile displays trade consistency.

---

## 4. Deterministic Metric Verification Scenarios

All 14 required hand-calculated deterministic scenarios are automated in [`tests/unit/test_phase_7_3_metrics.py`](file:///C:/Users/Hashray_X/MyTradeView/tests/unit/test_phase_7_3_metrics.py).

```
================================================================================
DETERMINISTIC TEST SUITE SUMMARY (14 SCENARIOS)
================================================================================
Case 01: All Winning Trades                         --> PASSED (Win Rate: 100.0%, PF: 999.99, DD: 0)
Case 02: All Losing Trades                          --> PASSED (Win Rate: 0.0%, PF: 0.00, DD: ₹1,080)
Case 03: Mixed Winning and Losing Trades            --> PASSED (Win Rate: 66.67%, PF: 4.38, DD: ₹840)
Case 04: Zero Trades (Empty Run)                    --> PASSED (All zeros, no ZeroDivisionError)
Case 05: Exactly One Trade                          --> PASSED (Win Rate: 100%, Sharpe: null)
Case 06: Zero-Variance Returns                      --> PASSED (Trade PnL / Stdev: null)
Case 07: Missing Observations / Data Gaps           --> PASSED (Missing data logged, no phantom fills)
Case 08: Period with No Trades                      --> PASSED (Daily P&L records active dates only)
Case 09: Open Position at End of Backtest           --> PASSED (EOD session cleanup liquidates at close)
Case 10: Partial Exit Handling                      --> PASSED (Order volume decomposed cleanly)
Case 11: Gross Profit Offset by Transaction Costs   --> PASSED (Gross +₹30, Costs ₹48 -> Net -₹18 [Loss])
Case 12: Drawdown Followed by Recovery              --> PASSED (Peak ₹102k -> ₹99k -> New High ₹103k)
Case 13: Multiple Consecutive Drawdowns             --> PASSED (Peak-to-trough max across 3 waves: ₹6k)
Case 14: Incomplete / Interrupted Backtest          --> PASSED (Empty input produces empty run)
Portfolio Sharpe Ratio Multi-Day                    --> PASSED (2 daily periods annualized with sqrt(252))
================================================================================
```

---

## 5. Audit of Costs & Simulated Execution Assumptions

The execution model ([`src/backtest/execution.py`](file:///C:/Users/Hashray_X/MyTradeView/src/backtest/execution.py)) strictly prevents double-counting and enforces Indian statutory derivative tax structures:

1. **Brokerage:** Flat ₹20.00 per executed order leg (₹40.00 roundtrip per trade).
2. **Securities Transaction Tax (STT):** Applied exclusively on the **SELL** side at 0.125% of turnover ($P_{\text{exit}} \times Q$).
3. **Stamp Duty:** Applied exclusively on the **BUY** side at 0.003% of turnover ($P_{\text{entry}} \times Q$).
4. **Exchange Turnover Charges (NSE):** 0.053% of turnover on both BUY and SELL legs.
5. **GST:** 18.0% applied strictly on $(\text{Brokerage} + \text{Exchange Turnover Charges})$.
6. **SEBI Regulatory Fee:** 0.0001% of turnover on both legs.
7. **Slippage Model:** Fixed points (default: 0.50 points) added to buy execution price, subtracted from sell execution price. Enforces minimum tick floor (₹0.05).
8. **Fill Timing:** Lookahead-free `next_bar_open` convention. Signals generated on bar $t$ close fill strictly at bar $t+1$ open.

---

## 6. Historical Market Data Inventory

An exhaustive repository search for historical market data files yielded the following inventory:

| Data Store / File | Instrument Type | Resolution | Date Range | Source / Provenance | Status / Feasibility |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **`SyntheticMarketDataBuilder`** | NIFTY Index & Weekly ATM CE/PE | 1-Minute | Single-session (2026-11-06) | Synthetic algorithmic generator | **SYNTHETIC ONLY** (Used for unit/integration tests) |
| **FYERS API v3 `/api/v3/history`** | Underlying Index (`NSE:NIFTY50-INDEX`) | 1-Minute | Multi-year historical | Live broker historical endpoint | **INDEX ONLY** (Cannot evaluate options strategy) |
| **FYERS API v3 `/api/v3/history`** | Expired Options Contracts | 1-Minute | Past weekly expiries | Live broker historical endpoint | **UNSUPPORTED** (Delisted upon expiry; returns code `-100`) |
| **Local Option CSV/Parquet Files** | Historical Weekly Option Candles | 1-Minute | Multi-month / multi-year | Not present in repository | **MISSING / UNAVAILABLE** |

### Data Feasibility Assessment:
As established in [`HISTORICAL_DATA_FEASIBILITY.md`](file:///C:/Users/Hashray_X/MyTradeView/HISTORICAL_DATA_FEASIBILITY.md), options backtesting cannot substitute underlying index candles for option prices without violating theta decay, implied volatility dynamics, and bid-ask spreads. Because authentic 1-minute historical option candle datasets are not present in the repository, historical strategy validation cannot be performed.

---

## 7. Strategy-Rule Verification Matrix

| Strategy Requirement | Code Implementation | Verification Test | Audit Status | Evidence / Notes |
| :--- | :--- | :--- | :---: | :--- |
| **Underlying Symbol** | `StrategyConfig.underlying_symbol` | `test_strategy_engine.py` | **PASS** | Set to `NSE:NIFTY50-INDEX`. |
| **Opening-Range Window** | [`src/strategy/opening_range.py`](file:///C:/Users/Hashray_X/MyTradeView/src/strategy/opening_range.py) | `test_opening_range_accumulation_and_boundary_modes` | **PASS** | Evaluated strictly 09:16 to 09:45 IST. |
| **Permitted Trading Days** | [`src/strategy/orb_nifty.py`](file:///C:/Users/Hashray_X/MyTradeView/src/strategy/orb_nifty.py#L75) | `test_trading_day_filter` | **PASS** | Enforces Monday, Thursday, Friday only. |
| **CE Upside Breakout** | `NiftyOrbStrategy._check_breakout` | `test_initial_upside_breakout_generates_long_ce` | **PASS** | Long CE signal on close > OR High. |
| **PE Downside Breakout** | `NiftyOrbStrategy._check_breakout` | `test_initial_downside_breakout_generates_long_pe` | **PASS** | Long PE signal on close < OR Low. |
| **Two-Phase Reversal** | [`src/oms/manager.py`](file:///C:/Users/Hashray_X/MyTradeView/src/oms/manager.py#L310) | `test_two_phase_reversal_sequencing` | **PASS** | Replacement entry placed only after exit fills. |
| **Daily MTM Loss Trigger** | [`src/risk/tracker.py`](file:///C:/Users/Hashray_X/MyTradeView/src/risk/tracker.py#L90) | `test_daily_loss_trigger_activation_and_closure_tracking` | **PASS** | ₹3,000 threshold locks out session. |
| **Profit-Protection Activation** | [`src/risk/profit_protection.py`](file:///C:/Users/Hashray_X/MyTradeView/src/risk/profit_protection.py#L65) | `test_profit_protection_default_lock_minimum_only` | **PASS** | Activates at +₹3,000; locks +₹500. |
| **Dynamic Lot Size** | [`src/data/contract_resolver.py`](file:///C:/Users/Hashray_X/MyTradeView/src/data/contract_resolver.py#L120) | `test_contract_resolver.py` | **PASS** | Dynamically resolved from instrument master. |
| **Mandatory EOD Square-Off** | `StrategyConfig.mandatory_exit_time` | `test_mandatory_eod_square_off_at_1510` | **PASS** | Flattens positions at 15:10 IST. |
| **StockMock Trailing Step** | `ProfitProtectionTracker` | `test_profit_protection_step_trailing` | **UNVERIFIED** | Default set to `LOCK_MINIMUM_ONLY`. Trailing behavior requires StockMock trade CSV. |

---

## 8. StockMock Comparison & Benchmarking Status

1. **Benchmark Engine Readiness:**  
   The StockMock benchmark parser and trade-matching comparator ([`src/backtest/benchmark.py`](file:///C:/Users/Hashray_X/MyTradeView/src/backtest/benchmark.py)) are implemented and tested with mock StockMock CSV exports (`test_stockmock_benchmark_matching_with_mock_csv`).
2. **Current Comparison Status:** **BLOCKED**
   - The user has not provided an authoritative exported StockMock CSV file containing historical trades.
   - Historical options data corresponding to past StockMock backtest dates is not present in the workspace.
   - Zero fabricated matches were performed. The API returns HTTP 400 with an explicit error when benchmark data is missing.

---

## 9. Backtest Reproducibility Audit

A reproducibility verification was conducted by executing identical synthetic datasets across multiple discrete backtest runs:
- **Test Command:** `pytest -k "test_replay_repeatability_produces_identical_metrics"`
- **Observed Behavior:** Consecutive runs across identical inputs produced 100% byte-for-byte identical trade timestamps, fill prices, slippage points, transaction taxes, equity curves, and summary metrics.
- **Run Metadata:** Every completed backtest persists its exact `strategy_config_snapshot`, `execution_config`, `dataset_id`, `start_date`, and `end_date` into the database.

---

## 10. Frontend Backtest UI Verification

The trading workstation backtesting interface ([`frontend/src/app/backtest/page.tsx`](file:///C:/Users/Hashray_X/MyTradeView/frontend/src/app/backtest/page.tsx)) was updated and verified:
1. **Accurate KPI Labels:** The KPI grid explicitly separates **`SHARPE RATIO (ANN.)`** from **`TRADE PnL / STDEV`**.
2. **Unavailable Indicators:** Single-session runs visibly display `"N/A"` for Sharpe ratio with an informative subtitle `"Unavailable (N/A)"`.
3. **Synthetic Test Data Banner:** Every synthetic backtest displays a prominent warning banner:
   `SYNTHETIC TEST DATA | Dataset: {dataset_id} | Strategy Version: v{version} | Range: ...`
4. **Execution Assumptions Displayed:** Slippage points (0.5 pts) and roundtrip brokerage (₹40/turn) are clearly displayed in the simulation header and trade journal.
5. **No False Parity Claims:** The benchmark tab clearly indicates that real StockMock CSV export data is required.

---

## 11. Remaining Defects & Unverified Assumptions

1. **Missing Real Historical Options Data:**  
   *Classification:* Environmental / Data Barrier.  
   *Details:* Neither FYERS API v3 nor local storage contains 1-minute historical candles for expired weekly option contracts.
2. **StockMock Trailing Step Unverified:**  
   *Classification:* Strategy Decision.  
   *Details:* Default set safely to `LOCK_MINIMUM_ONLY`. Trailing rule (+₹500 / +₹1,000) is supported via configuration but remains unverified against StockMock.
3. **Statutory Tax Rates:**  
   *Classification:* Market Operational Assumption.  
   *Details:* STT (0.125%), Exchange charges (0.053%), and GST (18%) reflect standard Indian derivatives schedules but may vary based on future regulatory revisions.

---

## 12. Final Readiness Decision

```
================================================================================
FINAL READINESS DECISION:
METRICS VERIFIED — HISTORICAL STRATEGY VALIDATION BLOCKED
================================================================================
```

### Definitive Justification:
1. **Metrics Engine Verified:** All 11 backtest metrics, including Sharpe ratio, trade consistency ratio, max drawdown, profit factor, win rate, and transaction costs, are mathematically sound, verified by 15 deterministic unit tests, and integrated seamlessly with the frontend.
2. **Safety Restrictions Maintained:** Execution mode remains strictly locked to `PAPER`. No broker order execution pathways were opened.
3. **Historical Validation Blocked:** Because FYERS API v3 does not provide historical candles for expired weekly options, and no authentic local option datasets exist in the repository, historical strategy profitability cannot be verified without external historical option data.

---
*Report prepared and verified by Antigravity Quantitative Pairing Assistant.*

