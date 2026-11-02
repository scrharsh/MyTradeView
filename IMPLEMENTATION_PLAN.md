# Implementation Plan: MyTradeView Execution Platform

## 1. Plan Overview

This roadmap defines the implementation path to build, verify, backtest, and run the StockMock-faithful NIFTY Opening Range Breakout (ORB) strategy using FYERS API v3. 

The implementation progresses in 10 sequential phases. **No live trading code or active broker orders will be initiated until all preliminary phases, automated test suites, simulation runs, and user approvals are complete.**

---

## 2. Phase-by-Phase Roadmap

### Phase 1: Core Domain Entities, Configuration & Database Foundations
- **Deliverables:**
  - `src/common/constants.py`: Enums for `ExecutionMode`, `OrderSide`, `OrderType`, `OrderStatus`, `ProductType`, `StrategyState`, `TradingDay`.
  - `src/core/models.py`: Pydantic / dataclass domain representations (`Tick`, `Candle`, `Signal`, `Order`, `Fill`, `Position`, `DailySession`).
  - `config/`: Validated schemas using `pydantic-settings` (`StrategySettings`, `BrokerSettings`, `RiskSettings`, `DatabaseSettings`).
  - `src/storage/db.py` & `src/storage/schema.py`: Async SQLAlchemy tables (`strategy_sessions`, `orders`, `fills`, `positions`, `daily_metrics`, `audit_events`).
  - Unit tests verifying database migrations, transactional rollbacks, and schema integrity under PostgreSQL and SQLite.
- **Acceptance Criteria:**
  - Configurations successfully validate from environment files with invalid values throwing strict Pydantic errors.
  - Asynchronous CRUD operations tested on SQLite and PostgreSQL.

---

### Phase 2: Market Data Layer & Option Symbol Resolver
- **Deliverables:**
  - `src/data/base.py`: Generic `MarketDataProvider` abstract interface.
  - `src/data/symbol_resolver.py`: 
    - Automated NIFTY weekly expiry date resolver (Thursday / pre-holiday adjustment).
    - ATM Strike calculation: $\text{round}(\text{Spot} / 50) \times 50$.
    - FYERS derivative symbol formatter (e.g., `NSE:NIFTY26N0624500CE`).
    - Local cache parser for official FYERS Symbol Master CSV (`https://public.fyers.in/sym_details/NSE_FO.csv`).
  - `src/data/csv_data.py`: Local historical 1-minute candle provider for backtesting.
  - Unit tests for ATM strike selection, leap years, monthly vs weekly format transitions, and holiday calendars.
- **Acceptance Criteria:**
  - Symbol resolver correctly outputs official FYERS contract symbols matching NSE derivative master across arbitrary historical and future dates.

---

### Phase 3: Strategy Engine & Opening Range Logic
- **Deliverables:**
  - `src/strategy/base.py`: Abstract Strategy base class.
  - `src/strategy/orb_nifty.py`:
    - Strict day-of-week filter (Monday, Thursday, Friday execution only).
    - Opening range builder: evaluates bars between 09:16:00 and 09:45:00 IST to establish `OR_HIGH` and `OR_LOW`.
    - Breakout detection: triggers `ENTER_LONG_CE` when NIFTY Spot breaks above `OR_HIGH`, and `ENTER_LONG_PE` when breaking below `OR_LOW`.
    - Reversal logic: If `LONG_CE` and price breaks `OR_LOW`, emit `CLOSE_CE` followed by `ENTER_LONG_PE`. If `LONG_PE` and price breaks `OR_HIGH`, emit `CLOSE_PE` followed by `ENTER_LONG_CE`.
    - EOD mandatory square-off signal emitted at 15:10:00 IST.
  - Comprehensive unit test suite covering range calculation, breakout triggers, reversal triggers, and day filtering.
- **Acceptance Criteria:**
  - Strategy engine produces $100\%$ deterministic signal outputs when replayed with mock tick/candle streams.

---

### Phase 4: Risk Management Engine & Circuit Breakers
- **Deliverables:**
  - `src/risk/engine.py`:
    - Real-time MTM tracker aggregating realized and unrealized PnL.
    - Daily Strategy Loss Kill Switch: Fires emergency shutdown when cumulative MTM $\le -₹3,000$.
    - Profit-Protection Controller: Activates when cumulative MTM $\ge +₹3,000$, enforcing a floor exit if MTM retreats to $+₹500$.
    - Trailing profit logic support (configurable step-based lock & trail).
  - `src/risk/pre_trade.py`: Pre-trade checks for order size, session validity, and duplicate submission blocks.
  - `src/risk/kill_switch.py`: Hard fail-safe handler to cancel open orders, exit active positions, and lock out trading.
- **Acceptance Criteria:**
  - Test suite simulates extreme negative slippage and sudden drawdowns, verifying that the kill switch terminates positions immediately and prevents subsequent entries.

---

### Phase 5: Backtesting Engine & StockMock Benchmark Calibration
- **Deliverables:**
  - `src/backtest/engine.py`: Discrete event-driven backtesting engine capable of replaying 1-minute NIFTY spot and options data.
  - `src/backtest/portfolio.py`: Simulated portfolio accounting for 1 lot entries, margin requirements, and realistic slippage.
  - `src/backtest/metrics.py`: Performance reporter computing Net PnL, Max Drawdown, Win Rate, Profit Factor, Average Win/Loss, and session logs.
  - StockMock comparison harness: Ingestion of StockMock backtest trade export CSVs to verify tick-by-tick parity.
- **Acceptance Criteria:**
  - Backtest results on sample historical period match StockMock trade-level entries, exits, and PnL within configured slippage tolerance.

---

### Phase 6: FYERS API v3 Broker Adapter
- **Deliverables:**
  - `src/broker/base.py`: Abstract broker interface.
  - `src/broker/auth.py`: OAuth 2.0 flow helper, token generator, and secure token cache manager.
  - `src/broker/fyers_adapter.py`:
    - REST client wrapping `/api/v3/orders/sync`, `/api/v3/orders`, `/api/v3/positions`, `/api/v3/history`, `/api/v3/quotes`.
    - Real-time WebSocket clients: `FyersDataSocket` for live market ticks, `FyersOrderSocket` for immediate execution updates.
    - Retry decorator with exponential backoff for transient network issues.
  - Mock FYERS server / responses for testing without calling live endpoints.
- **Acceptance Criteria:**
  - Adapter passes full integration suite against mock FYERS responses with zero unhandled exceptions or connection leakages.

---

### Phase 7: Order Management System (OMS) & Reconciliation Worker
- **Deliverables:**
  - `src/oms/order_manager.py`: State machine tracking order states (`NEW`, `TRANSIT`, `PENDING`, `FILLED`, `REJECTED`, `CANCELLED`).
  - `src/oms/idempotency.py`: Unique tag generation (`order_tag`) ensuring duplicate submissions are rejected at the client and broker level.
  - Atomic Reversal Execution Handler:
    1. Send exit order for current leg.
    2. Await verified fill confirmation (or timeout $\to$ aggressive IOC market exit).
    3. Send entry order for reverse leg only after confirmation.
  - `src/oms/reconciliation.py`: Periodic reconciliation worker polling broker positions (`GET /api/v3/positions`) every 5 seconds, flagging discrepancies.
- **Acceptance Criteria:**
  - State machine correctly handles partial fills, rejected orders, and out-of-order websocket events without entering invalid states.

---

### Phase 8: Scheduler, Session Orchestration & Notification System
- **Deliverables:**
  - `src/scheduler/session_manager.py`: Market session scheduler driving pre-market bootstrap, range formation, trading window, and EOD cleanup.
  - `src/scheduler/runner.py`: Top-level CLI and service orchestrator binding all modules together.
  - `src/monitoring/alerts.py`: Notification channels (Telegram, Slack, console) for trades, errors, and risk triggers.
  - `src/monitoring/health.py`: Process heartbeat and watchdog.
- **Acceptance Criteria:**
  - Service runs seamlessly through a simulated trading day lifecycle from 09:00:00 to 15:30:00 IST.

---

### Phase 9: Comprehensive Test Suite & Paper Trading Validation
- **Deliverables:**
  - Unit tests: $\ge 90\%$ code coverage across all core modules.
  - End-to-end simulation test: Full synthetic trading week replay containing gaps, volatile reversals, and limit hits.
  - Paper trading deployment: Running in paper mode (`EXECUTION_MODE=paper`) using live market feeds and simulated broker execution.
- **Acceptance Criteria:**
  - System executes in paper mode for multiple live market sessions without any crashes, desynchronization, or duplicate order submissions.

---

### Phase 10: Production Readiness & Live Trading Gate
- **Deliverables:**
  - Pre-flight verification checklist.
  - Live deployment guide with systemd service configuration.
  - Production database setup on PostgreSQL.
  - Secrets auditing to guarantee zero token leakage.
- **Acceptance Criteria:**
  - User review and explicit approval of all architecture documents, configurations, and risk controls before initiating live executor deployment.

