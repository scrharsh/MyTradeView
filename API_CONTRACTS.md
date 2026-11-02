# Backend API & WebSocket Contracts Specification: MyTradeView

## 1. Overview & Architecture

The `MyTradeView` backend provides a high-performance REST and WebSocket API (implemented via **FastAPI** and **Pydantic v2**) serving as the bridge between the Python trading core and the Next.js frontend workstation.

### Security & Safety Rules
1. **Zero Secret Exposure:** FYERS App ID, App Secret, and Access Tokens are stored securely in local encrypted files (`./secrets/fyers_token.json`) and are never exposed via REST or WebSockets.
2. **Fail-Closed Execution Gate:** Order execution endpoints strictly reject live trading requests unless the application is running with `EXECUTION_MODE=live` and `LIVE_TRADING_ACK=true`.
3. **Audit Traceability:** Every request altering strategy configuration, session state, or risk limits produces an immutable `AuditEventRecord` in PostgreSQL.

---

## 2. REST API Endpoints Specification

Base URL: `http://localhost:8000/api/v1`

### 2.1 Strategy Templates & Configuration Versioning

#### `GET /strategies/templates`
Retrieve all supported strategy templates and their parameter schemas.
- **Response (200 OK):**
```json
[
  {
    "template_type": "NIFTY_ORB",
    "display_name": "NIFTY Opening Range Breakout (ORB)",
    "description": "Breaks out of the 09:16-09:45 IST opening range with ATM weekly options and atomic reversals.",
    "supported_underlyings": ["NSE:NIFTY50-INDEX"],
    "supported_order_types": [1, 2],
    "parameters": {
      "orb_start_time": {"param_type": "str", "default_value": "09:16:00", "description": "Range start time (IST)"},
      "orb_end_time": {"param_type": "str", "default_value": "09:45:00", "description": "Range end time (IST)"},
      "mandatory_exit_time": {"param_type": "str", "default_value": "15:10:00", "description": "EOD exit time (IST)"},
      "trading_days": {"param_type": "list", "default_value": ["Monday", "Thursday", "Friday"]},
      "lots": {"param_type": "int", "default_value": 1},
      "breakout_confirmation_mode": {"param_type": "str", "default_value": "candle_close", "allowed_values": ["candle_close", "intrabar_tick"]},
      "max_daily_loss_limit": {"param_type": "Decimal", "default_value": "3000.00"},
      "profit_protection_trigger": {"param_type": "Decimal", "default_value": "3000.00"},
      "profit_protection_min_lock": {"param_type": "Decimal", "default_value": "500.00"}
    }
  }
]
```

#### `POST /strategies/validate`
Validate parameters against a template schema before saving.
- **Request Body:**
```json
{
  "template_type": "NIFTY_ORB",
  "parameters": {
    "orb_start_time": "09:16:00",
    "orb_end_time": "09:45:00",
    "trading_days": ["Monday", "Thursday", "Friday"],
    "lots": 1,
    "max_daily_loss_limit": "3000.00"
  }
}
```
- **Response (200 OK):**
```json
{
  "is_valid": true,
  "validated_parameters": { ... },
  "unresolved_decisions": []
}
```
- **Response (422 Unprocessable Entity):**
```json
{
  "is_valid": false,
  "errors": ["Unsupported parameter 'invalid_key' for template 'NIFTY_ORB'"]
}
```

#### `GET /strategies`
List all saved strategy configurations and their active versions.

#### `POST /strategies`
Create a new named strategy configuration (Version 1).

#### `POST /strategies/{config_id}/versions`
Create a new version of an existing strategy configuration with updated parameters.

---

### 2.2 Market Data & Candlesticks

#### `GET /market-data/candles`
Fetch historical or replay candlestick bars for charting.
- **Query Parameters:**
  - `symbol` (str, required): e.g. `NSE:NIFTY50-INDEX` or `NSE:NIFTY26N0624500CE`
  - `resolution` (str, default: `1m`): `1m`, `3m`, `5m`, `15m`
  - `from_time` (ISO 8601 UTC timestamp): e.g. `2026-11-06T03:45:00Z`
  - `to_time` (ISO 8601 UTC timestamp): e.g. `2026-11-06T09:40:00Z`
- **Response (200 OK):**
```json
{
  "symbol": "NSE:NIFTY50-INDEX",
  "resolution": "1m",
  "candles": [
    {
      "timestamp": "2026-11-06T03:46:00Z",
      "open": "24450.00",
      "high": "24500.00",
      "low": "24420.00",
      "close": "24460.00",
      "volume": 12500
    }
  ],
  "provenance": "OFFLINE_PARQUET",
  "data_quality": {
    "is_complete": true,
    "missing_bars_count": 0
  }
}
```

---

### 2.3 Trading Sessions & Strategy Execution State

#### `GET /sessions/active`
Retrieve current active market session status and telemetry.
- **Response (200 OK):**
```json
{
  "session_id": "SESS_20261106",
  "trading_date": "2026-11-06",
  "execution_mode": "paper",
  "phase": "ACTIVE_TRADING",
  "is_active": true,
  "started_at": "2026-11-06T03:30:00Z",
  "strategy_state": "LONG_CE",
  "opening_range": {
    "is_established": true,
    "high": "24500.00",
    "low": "24400.00",
    "range_width": "100.00",
    "candle_count": 29
  },
  "active_contract": {
    "symbol": "NSE:NIFTY26N0624500CE",
    "strike": "24500.00",
    "option_type": "CE",
    "expiry_date": "2026-11-06",
    "lot_size": 25
  }
}
```

---

### 2.4 Risk Monitoring & Kill Switch

#### `GET /risk/snapshot`
Retrieve real-time MTM, peak equity, drawdown, and circuit breaker status.
- **Response (200 OK):**
```json
{
  "session_id": "SESS_20261106",
  "timestamp": "2026-11-06T05:15:00Z",
  "realized_pnl": "625.00",
  "unrealized_pnl": "450.00",
  "total_mtm": "1075.00",
  "peak_mtm": "1200.00",
  "current_drawdown": "125.00",
  "is_kill_switch_active": false,
  "kill_switch_reason": null,
  "is_profit_protection_active": false,
  "profit_floor": "0.00",
  "open_positions_count": 1,
  "is_data_stale": false
}
```

#### `POST /risk/kill-switch`
Manual emergency trigger to immediately cancel working orders and liquidate positions.
- **Request Body:**
```json
{
  "reason": "MANUAL_OVERRIDE",
  "notes": "Emergency liquidation requested via frontend button"
}
```
- **Response (200 OK):**
```json
{
  "status": "TRIGGERED",
  "order_ids_cancelled": ["ORD_001"],
  "positions_flattened": ["NSE:NIFTY26N0624500CE"],
  "timestamp": "2026-11-06T05:16:10Z"
}
```

---

### 2.5 Orders & Positions

#### `GET /orders`
Query session orders filtered by status (`NEW`, `FILLED`, etc.).

#### `GET /positions`
Query active and historical positions with realized and unrealized P&L.

#### `POST /positions/{symbol}/square-off`
Request individual position closure via the pre-trade validated execution layer.

---

### 2.6 Backtesting Lab

#### `POST /backtest/run`
Trigger a backtest execution on a historical period.
- **Request Body:**
```json
{
  "config_id": "STRAT_NIFTY_ORB_v1",
  "start_date": "2026-10-01",
  "end_date": "2026-11-06",
  "slippage_points": "1.00",
  "brokerage_per_order": "20.00"
}
```
- **Response (202 Accepted):**
```json
{
  "backtest_id": "BT_20261102_001",
  "status": "RUNNING",
  "estimated_completion_seconds": 5
}
```

#### `GET /backtest/{backtest_id}`
Retrieve backtest summary, equity curve data points, and performance metrics.

---

## 3. Real-Time WebSocket Telemetry Protocol

Endpoint: `ws://localhost:8000/ws/telemetry`

### 3.1 Client Inbound Messages
Clients send subscription frames to filter event types:
```json
{
  "action": "SUBSCRIBE",
  "channels": ["market_data", "strategy_state", "orders", "risk_mtm"]
}
```

### 3.2 Server Outbound Event Frames

#### A. Market Tick (`TICK`)
```json
{
  "type": "TICK",
  "symbol": "NSE:NIFTY50-INDEX",
  "ltp": "24505.50",
  "timestamp": "2026-11-06T04:16:01.250Z"
}
```

#### B. Strategy Signal Emitted (`SIGNAL_EMITTED`)
```json
{
  "type": "SIGNAL_EMITTED",
  "signal_id": "SIG_SESS_0001",
  "signal_type": "ENTER_LONG_CE",
  "underlying_price": "24550.00",
  "target_contract": "NSE:NIFTY26N0624550CE",
  "reason": "NIFTY upside breakout above OR High (24500.00) at 24550.00",
  "sequence_number": 1,
  "timestamp": "2026-11-06T04:16:00Z"
}
```

#### C. Order State Update (`ORDER_STATE_UPDATE`)
```json
{
  "type": "ORDER_STATE_UPDATE",
  "client_order_id": "ORD_001",
  "broker_order_id": "261106000123",
  "from_status": "TRANSIT",
  "to_status": "FILLED",
  "filled_qty": 25,
  "avg_fill_price": "120.50",
  "timestamp": "2026-11-06T04:16:02Z"
}
```

#### D. Real-Time MTM Update (`MTM_SNAPSHOT`)
```json
{
  "type": "MTM_SNAPSHOT",
  "total_mtm": "1075.00",
  "realized_pnl": "625.00",
  "unrealized_pnl": "450.00",
  "current_drawdown": "125.00",
  "timestamp": "2026-11-06T04:16:05Z"
}
```

#### E. Risk Circuit Breaker Alert (`CIRCUIT_BREAKER_TRIGGERED`)
```json
{
  "type": "CIRCUIT_BREAKER_TRIGGERED",
  "reason": "DAILY_LOSS_LIMIT_BREACHED",
  "breach_mtm": "-3050.00",
  "loss_limit": "3000.00",
  "affected_positions": ["NSE:NIFTY26N0624500CE"],
  "timestamp": "2026-11-06T05:20:00Z"
}
```

