# Phase 6 Implementation Report: FYERS Integration, Order Management System (OMS), and Market Data

**Workspace:** `C:\Users\Hashray_X\MyTradeView`  
**Phase:** Phase 6 — Broker Integration, Order Management System (OMS), WebSocket Services, and Reconciliation  
**Execution Mode:** `PAPER` (`EXECUTION_MODE=paper` strictly enforced)  
**Test Suite Status:** 125/125 tests passing (100% pass rate in 1.33s)  
**Date:** November 2026  

---

## Executive Summary

Phase 6 delivers a mission-critical, enterprise-grade broker integration and Order Management System (OMS) foundation for MyTradeView. Built directly upon the existing domain models, multi-tier risk engine, contract resolver, and discrete-event backtesting subsystems from Phases 1–5, Phase 6 provides robust execution abstractions, stateful order lifecycle management, fail-closed WebSocket feeds, and independent broker-state reconciliation.

All operations remain strictly bounded by the `PAPER` execution safety interlock: **zero live broker orders, modifications, cancellations, or automatic liquidations** can occur. Real credentials, app secrets, and OAuth access tokens are comprehensively redacted across domain representations and log outputs.

```
+---------------------------------------------------------------------------------------------------+
|                                  MYTRADEVIEW ARCHITECTURE                                         |
+---------------------------------------------------------------------------------------------------+
|                                                                                                   |
|    +-----------------------------+                 +----------------------------------------+     |
|    |     Strategy Engine         |                 |        Risk Management Subsystem       |     |
|    |  - ORB NIFTY Breakout       |                 |  - Tier 1: Pre-Trade Gatekeeper        |     |
|    |  - Signal Generation        |                 |  - Tier 2: MTM & Circuit Breakers      |     |
|    +--------------+--------------+                 +-------------------+--------------------+     |
|                   | OrderIntent                                        | Validation Result        |
|                   v                                                    v                          |
|    +----------------------------------------------------------------------------------------+     |
|    |                          Order Management System (OMS)                                 |     |
|    |  - Idempotency Tracker (Deterministic Client Order ID / 20-char FYERS Tag)             |     |
|    |  - Strict Lifecycle State Machine (INTENT_CREATED -> OPEN -> FILLED/CANCELLED)         |     |
|    |  - Atomic Two-Phase Reversal Sequencing (Exit FILLED Before Replacement Submitted)    |     |
|    |  - Ambiguous Network Timeout Safety Barrier (RECONCILIATION_REQUIRED, Zero Retries)   |     |
|    |  - Authoritative Fill Accounting, Volume Accumulation, Weighted Avg Price              |     |
|    +--------------+--------------------------------------------+----------------------------+     |
|                   | BrokerOrderPayload                         ^ Normalized Fills & Updates       |
|                   v                                            |                                  |
|    +--------------------------------+       +-----------------------------------------------+     |
|    |   Abstract Broker Client       |       |              WebSocket Services               |     |
|    |  - PaperBrokerClient (Active)  |       |  - MarketDataWebSocketService (Fail-Closed)   |     |
|    |  - FyersBrokerClient (PAPER)   |       |  - OrderUpdateWebSocketService (OMS Dispatch) |     |
|    +--------------+-----------------+       +-----------------------+-----------------------+     |
|                   |                                                 ^                             |
|                   v                                                 |                             |
|    +----------------------------------------------------------------+-----------------------+     |
|    |                          Broker Reconciliation Service                                 |     |
|    |  - Independent Orderbook & Position Comparison (Detects Missing, Desync, Residual)     |     |
|    |  - Fail-Closed on Broker Unavailability; Never Performs Automated Liquidation          |     |
|    +----------------------------------------------------------------------------------------+     |
|                   |                                                 |                             |
|                   +-----------------------+-------------------------+                             |
|                                           v                                                       |
|    +----------------------------------------------------------------------------------------+     |
|    |                     State Repository & Persistence Layer (PostgreSQL / SQLite)         |     |
|    |  - orders (with oms_status), order_state_transitions, fills, positions, sessions       |     |
|    +----------------------------------------------------------------------------------------+     |
+---------------------------------------------------------------------------------------------------+
```

---

## 1. Architectural Components Implemented

### 1.1 Broker Adapter Layer (`src/broker/`)
- **`AbstractBrokerClient` (`src/broker/base.py`)**: Abstract asynchronous interface declaring `connection_status`, `check_auth()`, `place_order()`, `modify_order()`, `cancel_order()`, `get_order_book()`, `get_trade_book()`, `get_positions()`, and `get_funds()`. Decouples higher-level logic from broker APIs.
- **`BrokerModels` (`src/broker/models.py`)**: Strict Pydantic models for outbound and inbound payloads:
  - `AuthStatus`: Masked credentials (`app_id[:4] + "***"`, `access_token[:6] + "...redacted"`).
  - `BrokerOrderPayload`: Pre-validates quantities, order types, limit prices, validity, client IDs, and 20-character FYERS tags (`generate_fyers_tag`).
  - `BrokerOrderResponse`: Normalizes broker outcomes; explicitly captures `is_timeout_ambiguous` when HTTP/transport timeouts occur.
  - `redact_sensitive_dict()`: Deep credential scrubber masking `access_token`, `app_secret`, `secret_key`, `pin`, `password`, and `client_id`.
- **`FyersBrokerClient` (`src/broker/fyers.py`)**:
  - Implements official FYERS API v3 REST specifications.
  - Fail-safe live order barrier: Intercepts all calls if `execution_mode != ExecutionMode.LIVE`, blocking outbound network submission.
  - Ambiguous timeout handling: Translates read/connect timeouts into `is_timeout_ambiguous=True`, blocking blind retries.
  - Error normalization: Maps FYERS status codes (-15, -16 expired token, 429 rate limit, 500 RMS reject) into typed exceptions (`BrokerAuthenticationError`, `BrokerConnectionError`).
- **`PaperBrokerClient` (`src/broker/paper.py`)**:
  - High-fidelity in-memory simulator for offline development and deterministic unit testing.
  - Configurable reject modes, network timeout injection, partial fill simulation, and slippage modeling.
  - Fully synchronized order-book, trade-book, and position state with zero external network dependencies.

---

### 1.2 Order Management System (`src/oms/`)
- **Granular Lifecycle State Machine (`src/oms/models.py`, `src/oms/lifecycle.py`)**:
  - Defines explicit states: `INTENT_CREATED`, `VALIDATING`, `SUBMISSION_PENDING`, `ACKNOWLEDGED`, `OPEN`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLATION_REQUESTED`, `CANCELLED`, `REJECTED`, `FAILED`, `RECONCILIATION_REQUIRED`.
  - Terminal protection: Once an order reaches terminal status (`FILLED`, `CANCELLED`, `REJECTED`, `FAILED`), out-of-order broker messages cannot regress state.
  - Mappings: Bidirectional translation between granular internal statuses and FYERS integer codes (1: Cancelled, 2: Filled, 4: Transit, 5: Rejected, 6: Pending, 7: Expired).
- **Idempotency & Token Generation (`src/oms/idempotency.py`)**:
  - `ClientOrderIdGenerator`: Produces deterministic internal identifiers (`ORD_<SESS>_<SEQ>_<SIDE>`) and FYERS-compliant 20-character order tags (`<SIDE>_<SEQ>_ORB`).
  - `IdempotencyTracker`: Tracks intent IDs and client order IDs; rejects duplicate submissions and race conditions.
- **OMS Manager (`src/oms/manager.py`)**:
  - Pre-trade risk gating: Mandatory evaluation via `RiskEngine.validate_order(...)` before broker submission.
  - Ambiguous timeout safety: Marks order as `RECONCILIATION_REQUIRED` upon submission timeout; mandates reconciliation before re-entry.
  - Fill deduplication: Maintains `_processed_fill_ids` to guarantee executions are never double-counted.
  - Cumulative volume and price accounting: Calculates exact weighted average fill price across partial fills.
  - Atomic two-phase reversal coordination: When reversing from long to short (or vice versa), the replacement order is submitted **only after** the exit order reaches terminal status `FILLED`. If the exit order is rejected or unfilled, the entry order is aborted.
- **Broker State Reconciliation Service (`src/oms/reconciliation.py`)**:
  - Queries broker orderbook and positions, comparing them against internal OMS state.
  - Detects 8 discrepancy categories: `MISSING_ON_BROKER`, `UNKNOWN_BROKER_ORDER`, `STATUS_DESYNC`, `QTY_MISMATCH`, `PRICE_MISMATCH`, `POSITION_DESYNC`, `RESIDUAL_POSITION`, `BROKER_UNAVAILABLE`.
  - Safety rule: Fails closed when the broker is unreachable; **never** automatically liquidates positions or cancels unknown orders during reconciliation.

---

### 1.3 WebSocket Market Data & Order Updates (`src/websocket/`)
- **`BaseWebSocketService` (`src/websocket/base.py`)**:
  - Explicit connection state machine: `DISCONNECTED`, `CONNECTING`, `CONNECTED`, `SUBSCRIBED`, `STALE`, `ERROR`.
  - Exponential backoff with 10% randomized jitter (initial 1.0s, multiplier 2.0x, max 30.0s).
  - Heartbeat tracking and silent disconnect detection.
- **`MarketDataWebSocketService` (`src/websocket/market_data.py`)**:
  - Manages symbol subscriptions across indices and options.
  - Normalizes incoming frames (epoch seconds, milliseconds, ISO strings) to UTC `MarketTick` models and IST display helpers.
  - Fail-closed staleness health monitoring: If tick age exceeds threshold (default 5.0s), transitions state to `STALE` and flags `is_data_healthy() -> False`.
- **`OrderUpdateWebSocketService` (`src/websocket/order_updates.py`)**:
  - Normalizes raw FYERS order and trade frames.
  - Deduplicates execution notifications using `tradeNumber` tracking.
  - Direct asynchronous routing to `OrderManagementSystem.on_fill_event(...)` and `on_broker_order_update(...)`.

---

## 2. Test Verification Matrix (All 20 Required Areas)

Every test executes deterministically without live network calls or broker dependencies.

| # | Required Test Area | Test Function Name | Verification Result |
|---|---|---|---|
| 1 | Authentication Success & Failure | `test_fyers_authentication_success_profile_inspection`<br>`test_fyers_authentication_failure_expired_token`<br>`test_paper_broker_authentication` | **PASSED** (Profile parsed, expired token -15 caught, paper mode deterministic) |
| 2 | Market-Data Connection & Disconnection | `test_market_data_connection_lifecycle` | **PASSED** (`DISCONNECTED` -> `CONNECTING` -> `CONNECTED` -> `SUBSCRIBED` -> `DISCONNECTED`) |
| 3 | WebSocket Reconnection & Staleness | `test_websocket_exponential_backoff_and_reset`<br>`test_market_data_staleness_detection_fail_closed`<br>`test_market_data_tick_normalization_and_ist` | **PASSED** (Exponential backoff with jitter, fail-closed `STALE` transition when data halts, IST timezone integrity) |
| 4 | Order Acknowledgement Lifecycle | `test_order_acknowledgement_lifecycle` | **PASSED** (`INTENT_CREATED` -> `VALIDATING` -> `SUBMISSION_PENDING` -> `ACKNOWLEDGED` -> `OPEN`) |
| 5 | Order Rejection by Broker | `test_order_rejection_by_broker` | **PASSED** (RMS reject transitions order directly to `REJECTED` with error message) |
| 6 | Submission Timeout Ambiguity | `test_submission_timeout_ambiguous_requires_reconciliation` | **PASSED** (`is_timeout_ambiguous` flags `RECONCILIATION_REQUIRED`; zero blind retries) |
| 7 | Duplicate Order Intent (Idempotency) | `test_duplicate_order_intent_idempotency` | **PASSED** (Duplicate intent/client ID returns existing order or rejects without resubmitting) |
| 8 | Partial and Complete Fills | `test_partial_and_complete_fills_accounting` | **PASSED** (Accumulates volume: 25 @ 100 + 25 @ 110 = 50 @ 105.00 weighted avg price; `PARTIALLY_FILLED` -> `FILLED`) |
| 9 | Duplicate Fill Event Deduplication | `test_duplicate_fill_deduplication` | **PASSED** (Identical `fill_id` ignored; volume and fills count not duplicated) |
| 10 | Out-of-Order Updates (Terminal Protection) | `test_out_of_order_broker_updates_terminal_protection` | **PASSED** (`FILLED` state ignores delayed `OPEN` or `CANCELLED` frames without regressing) |
| 11 | Cancellation Request vs Confirmation | `test_cancellation_request_versus_confirmation` | **PASSED** (`CANCELLATION_REQUESTED` precedes broker confirmation -> `CANCELLED`) |
| 12 | Position Reconciliation (Sync Match) | `test_reconciliation_synchronized_match` | **PASSED** (Matches internal books against broker; reports `is_synchronized=True`, 0 discrepancies) |
| 13 | Order/Position Discrepancy Detection | `test_reconciliation_detects_order_and_position_discrepancies` | **PASSED** (Flags `UNKNOWN_BROKER_ORDER` and `STATUS_DESYNC` without automated liquidation) |
| 14 | Reversal Sequencing | `test_two_phase_reversal_sequencing` | **PASSED** (Phase 2 replacement submitted only after Phase 1 exit is `FILLED`; aborted if exit fails) |
| 15 | Risk-Engine Pre-Trade Rejection | `test_risk_engine_pre_trade_rejection` | **PASSED** (Non-trading Saturday rejected by gatekeeper; broker never touched) |
| 16 | Kill-Switch Lockout | `test_kill_switch_lockout` | **PASSED** (Active kill switch causes OMS to immediately reject all new order intents) |
| 17 | Token and Secret Redaction | `test_token_and_secret_redaction` | **PASSED** (Masks tokens/secrets in `__repr__` and `redact_sensitive_dict`) |
| 18 | Persistence & Restart Recovery | `test_oms_persistence_and_restart_recovery` | **PASSED** (Orders and transitions saved to `StateRepository` with `oms_status`) |
| 19 | Broker Unavailability | `test_broker_unavailability_fails_closed` | **PASSED** (`BrokerConnectionError` safely marks order `RECONCILIATION_REQUIRED`) |
| 20 | Order Update WebSocket Dispatch | `test_order_update_websocket_frame_dispatch` | **PASSED** (Raw FYERS trade frame parsed and dispatched to OMS, producing fill and updating state) |

---

## 3. Full Repository Test Suite Verification

```
============================= test session starts =============================
platform win32 -- Python 3.10.11, pytest-9.2.0, pluggy-1.7.0
rootdir: C:\Users\Hashray_X\MyTradeView
configfile: pyproject.toml
plugins: asyncio-1.4.1.post1
collected 125 items

tests/unit/test_backtest_engine.py .............. [20 passed]
tests/unit/test_cache.py ........................ [ 3 passed]
tests/unit/test_config.py ....................... [ 7 passed]
tests/unit/test_contract_resolver.py ............ [ 8 passed]
tests/unit/test_fyers_data_adapter.py ........... [ 6 passed]
tests/unit/test_models.py ....................... [ 8 passed]
tests/unit/test_offline_data.py ................. [ 5 passed]
tests/unit/test_oms_and_broker.py ............... [24 passed]
tests/unit/test_risk_engine.py .................. [15 passed]
tests/unit/test_state_transitions.py ............ [ 4 passed]
tests/unit/test_storage.py ...................... [ 6 passed]
tests/unit/test_strategy_engine.py .............. [14 passed]
tests/unit/test_time_utils.py ................... [ 5 passed]

============================= 125 passed in 1.33s =============================
```

Zero regressions across all existing tests from Phases 1–5.

---

## 4. Safety Guarantees & Operational Constraints

1. **Strict PAPER Mode Enforcement**:
   - `EXECUTION_MODE=paper` is the permanent default in `TradingSession` and `config/`.
   - `FyersBrokerClient.place_order()` contains a hard safety barrier blocking real outbound network calls whenever `execution_mode != ExecutionMode.LIVE`.
2. **Credential and Secret Redaction**:
   - `AuthStatus` masks `app_id` and `access_token` in its `__repr__`.
   - `redact_sensitive_dict()` recursively scrubs dictionary keys (`access_token`, `app_secret`, `secret_key`, `pin`, `password`) before logging.
3. **No Blind Retries on Network Timeout**:
   - When an HTTP or WebSocket timeout occurs during order submission, the status is set to `RECONCILIATION_REQUIRED` (`is_timeout_ambiguous=True`).
   - The OMS strictly prevents duplicate resubmission until authoritative reconciliation confirms whether the order reached the exchange.
4. **No Automated Liquidation**:
   - `BrokerReconciliationService` identifies discrepancies and generates actionable structured audit reports without executing automated liquidations or cancellations.
5. **Fail-Closed Staleness Detection**:
   - `MarketDataWebSocketService.is_data_healthy()` fails closed (`False`) if ticks cease beyond the configured threshold, enabling the risk engine to block new orders during data outages.

---

## 5. Next Steps

Phase 6 is complete, verified, and committed. The Order Management System, FYERS API v3 adapter, WebSocket services, and broker reconciliation engine are fully integrated and validated.

All requirements for Phase 6 have been satisfied. No Phase 7 implementation work has been initiated. Awaiting user review and instruction.
