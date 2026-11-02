# Phase 7 Implementation Report: Trading Workstation Frontend and Backend API Integration

**Workspace:** `C:\Users\Hashray_X\MyTradeView`  
**Phase:** Phase 7 — Trading Workstation Frontend (Next.js 14 App Router, TypeScript, Tailwind CSS, Lightweight Charts) & Backend REST/WebSocket API  
**Execution Mode:** `PAPER` (`EXECUTION_MODE=paper` strictly enforced across backend and frontend)  
**Test Suite Status:** 140/140 tests passing (100% pass rate in 2.09s)  
**Frontend Production Build:** `next build` completed with 0 errors across 12 static/dynamic routes  
**Date:** November 2026  

---

## 1. Executive Summary

Phase 7 delivers a institutional algorithmic trading workstation for `MyTradeView`. The platform bridges the underlying Python engine, FYERS OMS abstractions, risk controls, and discrete-event backtesting subsystems (Phases 1–6) into a high-density, real-time financial workstation.

Built with **Next.js 14 (App Router)**, **TypeScript**, **Tailwind CSS**, and **TradingView Lightweight Charts v4**, the frontend interfaces with a high-performance **FastAPI REST & WebSocket** backend API (`src/api/`).

All operations remain strictly locked into **`PAPER`** execution mode (`EXECUTION_MODE=paper`). Live broker order submission, live modifications, and live liquidation routes remain disabled. Secrets, broker app secrets, and tokens are redacted.

```
+---------------------------------------------------------------------------------------------------+
|                                  MYTRADEVIEW WORKSTATION ARCHITECTURE                             |
+---------------------------------------------------------------------------------------------------+
|                                                                                                   |
|    +----------------------------------------------------------------------------------------+     |
|    |           NEXT.JS 14 FINANCIAL TRADING WORKSTATION (PORT 3000)                         |     |
|    |  - Global Status Bar (Institutional PAPER MODE Badge, IST Clock, Session MTM Pill)      |     |
|    |  - Dual-Panel Canvas (TradingView Lightweight Charts: NIFTY 50 Spot 1m + Option 1m)    |     |
|    |  - Backtesting Lab & StockMock Benchmark Comparator (Interactive Equity Curve)         |     |
|    |  - Strategy Builder (Version Control & Parameter Diffs)                                |     |
|    |  - Paper Trading Console (State Machine Visualizer & Operator Controls)                |     |
|    |  - Orders & Positions (Lifecycle Transitions & Square-Off Actions)                     |     |
|    |  - Risk Management & Audit Control (Breaker Gauges & Broker Reconciliation)             |     |
|    |  - Settings & System Diagnostics (Execution Locked to PAPER Mode)                      |     |
|    +-----------------------------+-----------------------------+----------------------------+     |
|                                  | REST API Calls (JSON)       ^ WebSocket Telemetry Stream       |
|                                  v                             |                                  |
|    +----------------------------------------------------------------------------------------+     |
|    |                   FASTAPI BACKEND SERVICE LAYER (PORT 8000, src/api/)                  |     |
|    |  - REST Endpoints: /health, /strategies, /market-data, /sessions, /risk, /orders,      |     |
|    |                    /backtest, /reconciliation                                          |     |
|    |  - WebSocket Endpoint: /ws/telemetry (Real-Time Ticks, MTM, Fills, Transitions)        |     |
|    |  - WorkstationService Singleton (Mediator for OMS, Risk Engine, State Repository)      |     |
|    +-----------------------------+-----------------------------+----------------------------+     |
|                                  |                             |                                  |
|                                  v                             v                                  |
|    +-----------------------------+---------+     +-------------+----------------------------+     |
|    |     Order Management System (OMS)     |     |          Risk Management Engine          |     |
|    |  - PaperBrokerClient (FYERS Adapter)  |     |  - ₹3,000 Hard Daily Loss Circuit Breaker|     |
|    |  - Idempotent Lifecycle State Machine |     |  - ₹3k Trigger / ₹500 Lock Profit Floor  |     |
|    |  - Atomic Two-Phase Reversal Pipeline |     |  - Emergency Kill-Switch Coordinator     |     |
|    +-----------------------------+---------+     +-------------+----------------------------+     |
|                                  |                             |                                  |
|                                  +--------------+--------------+                                  |
|                                                 v                                                 |
|    +----------------------------------------------------------------------------------------+     |
|    |                     State Repository & Persistence Layer (PostgreSQL / SQLite)         |     |
|    |  - strategy_configs, trading_sessions, broker_orders, fills, positions, backtests       |     |
|    +----------------------------------------------------------------------------------------+     |
+---------------------------------------------------------------------------------------------------+
```

---

## 2. Workstation Pages & Component Architecture

### 2.1. Header & Global Status Bar (`Header.tsx` & `WorkstationShell.tsx`)
- **Institutional Execution Badge:** Glowing green `PAPER MODE` indicator permanently displayed.
- **Ticking IST Clock:** Real-time digital clock formatted strictly in `Asia/Kolkata` (IST) with hour/minute/second precision.
- **Session Telemetry Pill:** Live net MTM display with dynamic profit/loss coloring (`getPnlColor`), opening range status (24,962.10 - 25,045.50), and active session phase (`ACTIVE_TRADING`).
- **Infrastructure Status:** Triple-node health diagnostics indicator checking Broker API, Database Persistence, and OMS Reconciliation synchronization.
- **Emergency Kill-Switch CTA:** High-visibility, prominent button opening the modal protocol.

### 2.2. Emergency Kill-Switch Protocol (`KillSwitchModal.tsx`)
- **Safety Interlock:** Requires the operator to explicitly type `"CONFIRM"` into an input box before the button activates.
- **Orchestration:** Calls `POST /api/v1/risk/kill-switch`, immediately cancelling all active/open orders across the OMS and broker, liquidating all open derivative positions at market, and locking strategy execution into a locked-out state.

### 2.3. Dashboard (`/dashboard`)
- **Executive KPI Cards:**
  - Net Session MTM (realized + unrealized breakdown).
  - Profit Protection Floor (Armed ₹3,000 trigger / ₹500 floor lock).
  - Daily Loss Breaker (₹3,000 threshold with remaining loss buffer).
  - Session & ORB Status (established 09:16–09:45 interval bounds).
- **Live Positions Table:** Displays net quantity, average entry price, current LTP, realized/unrealized P&L, and an inline single-click **Square-Off** action button.
- **Market & Infrastructure Telemetry:** Real-time underlying spot index quote (`NSE:NIFTY50-INDEX`), resolved option contract (`NSE:NIFTY26NOV25000CE`), and component health.
- **Recent Orders Table:** Displays client order ID, broker order ID, side, filled/requested quantity, order type, status pill, and submission timestamp.

### 2.4. Strategy Builder & Version Control (`/strategies`)
- **Domain Parameter Form:** Full configuration controls for Opening Range (09:16–09:45), Breakout Mode (`candle_close` vs `wick_touch`), Boundary Mode (`up_to_0944_close` vs `up_to_0945_close`), Trading Schedule (Mon, Thu, Fri), Profit Protection (₹3,000 trigger, ₹500 lock, ₹1,000 step trailing), and Max Daily Loss limit (₹3,000).
- **Real-Time Domain Validation:** Automatically submits parameters to `POST /api/v1/strategies/validate`, returning validation status, domain violations, and StockMock scheduling advisories.
- **Version History & Parameter Diff Viewer:** Displays chronological snapshots of strategy versions and shows parameter diffs side-by-side against the saved version.
- **Save as New Version:** Commits verified configurations into persistent storage via `POST /api/v1/strategies/{id}/versions`.

### 2.5. Interactive Charts (`/charts`)
- **TradingView Lightweight Charts v4:** Pure HTML5 Canvas implementation dynamically imported (`ssr: false`) with responsive resize observers.
- **Dual-Panel Architecture:**
  - *Upper Panel:* NIFTY 50 Spot 1m candlestick series + ORB High (25,045.50) & ORB Low (24,962.10) horizontal price lines + Long Entry arrow marker at 09:46.
  - *Lower Panel:* ATM Option (`NSE:NIFTY26NOV25000CE`) 1m candlestick series + Entry fill price line (₹143.00).
- **Synchronized Crosshair & HUD:** Real-time crosshair listener updating an institutional OHLC header displaying Date/Time (IST), Open, High, Low, Close, and Volume.

### 2.6. Discrete-Event Backtesting Lab (`/backtest`)
- **Simulation Launcher:** Configure strategy version, date range, initial capital, slippage model (points), and broker commission (₹40/round-turn).
- **Summary Performance KPI Grid:** Net Profit, Win Rate %, Profit Factor, Max Drawdown (₹ and %), Total Trades, Expectancy, and Sharpe Ratio.
- **Interactive Portfolio Equity Curve:** Lightweight Charts area series visualizing equity progression.
- **Trade Journal Table:** Trade ID, Instrument, Side, Entry/Exit Timestamps, Entry/Exit Prices, Exit Reason (`PROFIT_PROTECTION`, `TARGET`, `STOP_LOSS`, `EOD`), Trading Costs, and Net P&L.
- **StockMock Benchmark Comparison:** Paste or upload StockMock CSV exports to run automated trade-by-trade comparison (`POST /api/v1/backtest/benchmark`), highlighting matched trades, timing/price discrepancies, and net P&L delta.

### 2.7. Paper Trading Console (`/paper`)
- **State Machine Visualizer:** 5-stage graphical progression tracking the ORB lifecycle:
  1. `09:16-09:45 ORB BUILDING`
  2. `ORB ESTABLISHED`
  3. `BREAKOUT ENTRY`
  4. `POSITION ACTIVE (PROFIT TRAILING)`
  5. `SQUARED OFF / EOD`
- **Real-Time Strategy & OMS Event Log:** Dense terminal feed logging tick ingestion, range establishment, breakout detection, contract resolution, order validation, fill matching, and risk evaluation.
- **Operator Controls:** Pause/Resume engine, Square-Off All positions, and trigger emergency kill-switch.

### 2.8. Orders & Positions Audit (`/orders`)
- **Order Lifecycle Inspector Modal:** Clicking any order displays its full state machine journey (`INTENT_CREATED` -> `RISK_VALIDATED` -> `BROKER_ACCEPTED` -> `FILLED`) alongside matched execution fills and average fill prices.
- **Filtering & Search:** Real-time filter tabs (`ALL`, `FILLED`, `OPEN`, `CANCELLED`, `REJECTED`) and instantaneous search by Client Order ID, Broker Order ID, or Symbol.
- **Portfolio Liquidation:** Dedicated positions view with single-click market square-off.

### 2.9. Risk Management & Audit Control (`/risk`)
- **Daily Loss Circuit Breaker Gauge:** Visual progress bar tracking current session MTM against the hard ₹3,000 loss limit and remaining buffer.
- **Profit Floor Indicator:** Displays current locked floor (₹500+), activation trigger (₹3,000), and trailing rules.
- **OMS vs Broker Reconciliation Audit:** Comparison engine validating in-memory order/position states against broker books, reporting discrepancy counts and synchronization status.
- **Immutable Audit Trail:** Append-only persistence audit table logging all system events with timestamps, actors, and details.

### 2.10. Settings & System Diagnostics (`/settings`)
- **Execution Mode Safety Notice:** Clearly highlights that execution is locked to `PAPER MODE`, and that live broker order gateways are disabled.
- **Broker Diagnostics (FYERS API v3):** Displays sanitized connection health, paper client status, model slippage, and latency metrics without exposing credentials.
- **Database & Storage Status:** Reports PostgreSQL/SQLite active connection, table integrity, and snapshot status.

---

## 3. Backend API Implementation (`src/api/`)

The FastAPI application matches the specifications defined in `API_CONTRACTS.md`:

| Route | Method | Description | Response Model |
| :--- | :--- | :--- | :--- |
| `/api/v1/health` | `GET` | Health, execution mode, broker/DB connectivity | `SystemHealthResponse` |
| `/api/v1/strategies/templates` | `GET` | Available strategy templates and parameter specs | `List[StrategyTemplateResponse]` |
| `/api/v1/strategies/validate` | `POST` | Validate strategy rules against domain constraints | `StrategyValidateResponse` |
| `/api/v1/strategies` | `GET` | List saved strategy configurations & versions | `List[StrategyConfigVersionResponse]` |
| `/api/v1/strategies` | `POST` | Create a new strategy configuration | `StrategyConfigVersionResponse` |
| `/api/v1/strategies/{id}/versions` | `POST` | Create a new version for a strategy config | `StrategyConfigVersionResponse` |
| `/api/v1/market-data/candles` | `GET` | Retrieve 1m historical candlesticks | `CandlesResponse` |
| `/api/v1/market-data/instruments` | `GET` | Master list of underlying and option contracts | `List[InstrumentResponseItem]` |
| `/api/v1/market-data/status` | `GET` | Provider status, stale detection, cache stats | `Dict[str, Any]` |
| `/api/v1/sessions/active` | `GET` | Current trading session, phase, opening range | `SessionActiveResponse` |
| `/api/v1/sessions/start` | `POST` | Initialize a new paper trading session | `Dict[str, str]` |
| `/api/v1/sessions/stop` | `POST` | Terminate active trading session | `Dict[str, str]` |
| `/api/v1/risk/snapshot` | `GET` | Live MTM, drawdown, circuit breaker, profit floor | `RiskSnapshotResponse` |
| `/api/v1/risk/kill-switch` | `POST` | Emergency liquidation & execution lockout | `KillSwitchResponse` |
| `/api/v1/orders` | `GET` | List orders with state transitions & fills | `List[OrderResponseItem]` |
| `/api/v1/positions` | `GET` | List active derivative positions and P&L | `List[PositionResponseItem]` |
| `/api/v1/positions/{symbol}/square-off` | `POST` | Square off position at paper market price | `PositionResponseItem` |
| `/api/v1/backtest/run` | `POST` | Run discrete-event backtest simulation | `BacktestDetailsResponse` |
| `/api/v1/backtest/runs` | `GET` | Historical backtest run summaries | `List[BacktestSummaryResponse]` |
| `/api/v1/backtest/{id}` | `GET` | Backtest analytics, equity curve, trade journal | `BacktestDetailsResponse` |
| `/api/v1/backtest/benchmark` | `POST` | Compare backtest run against StockMock CSV | `BenchmarkResponse` |
| `/api/v1/reconciliation/run` | `POST` | Execute OMS vs Broker reconciliation audit | `ReconciliationReport` |
| `/api/v1/reconciliation/latest` | `GET` | Retrieve latest reconciliation audit report | `ReconciliationReport` |
| `/ws/telemetry` | `WebSocket` | Real-time tick events, MTM, and order updates | Streaming JSON |

---

## 4. Verification and Quality Assurance

### 4.1. Production Frontend Build
```powershell
> next build
▲ Next.js 14.2.35
✓ Compiled successfully
✓ Linting and checking validity of types ...
✓ Collecting page data ...
✓ Generating static pages (12/12) ...
✓ Finalizing page optimization ...

Route (app)                              Size     First Load JS
┌ ○ /                                    138 B          87.5 kB
├ ○ /_not-found                          873 B          88.2 kB
├ ○ /backtest                            6.43 kB        93.8 kB
├ ○ /charts                              3.63 kB          91 kB
├ ○ /dashboard                           5.48 kB        92.8 kB
├ ○ /orders                              5.26 kB        92.6 kB
├ ○ /paper                               5.22 kB        92.6 kB
├ ○ /risk                                5.37 kB        92.7 kB
├ ○ /settings                            3.76 kB        91.1 kB
└ ○ /strategies                          5.22 kB        92.6 kB
+ First Load JS shared by all            87.3 kB
```
**Result:** 0 TypeScript errors, 0 ESLint errors, clean bundle compilation.

### 4.2. Complete Backend Test Suite
```powershell
.venv\Scripts\pytest -v
======================= 140 passed, 1 warning in 2.09s =======================
```
- **Total Tests:** 140 passing
- **New API Tests (`tests/unit/test_api.py`):** 15 tests covering all REST endpoints, WebSocket telemetry, validation logic, and error boundaries.
- **Existing Tests:** 125 tests covering OMS, Broker, Risk Engine, Strategy Engine, Storage, Backtest, and Time Utilities.
- **Regressions:** Zero regressions.

---

## 5. Scope & Safety Compliance Audit

| Requirement | Implementation Status | Verification Evidence |
| :--- | :--- | :--- |
| **Execution Mode Locked to PAPER** | Confirmed | `EXECUTION_MODE=paper` strictly enforced across backend services and UI badges. |
| **Live Order Execution Prohibited** | Confirmed | All broker order paths route to `PaperBrokerClient`. Live trading tab is explicitly disabled. |
| **Credential & Secret Protection** | Confirmed | Zero passwords, client secrets, or OAuth tokens exposed in UI or logs; fully redacted. |
| **No Strategy Alteration** | Confirmed | ORB rules (09:16–09:45, Mon/Thu/Fri, ₹3k trigger / ₹500 floor) preserved without modification. |
| **Design Integrity** | Confirmed | Dense, professional dark-mode financial terminal styling using custom palette and typography. |

---

## 6. Phase 7 Sign-Off

Phase 7 is complete. The trading workstation frontend and backend API layers are tested, verified, and operational in `PAPER` mode.

*Phase 7 complete.*
