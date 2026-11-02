# Configuration Reference Specification: MyTradeView

## 1. Overview

`MyTradeView` utilizes **Pydantic v2 Settings** (`pydantic-settings`) for unified, validated configuration management. Configuration is ingested from environment variables and an optional `.env` file located in the project root.

All parameters are strictly typed, checked at application boot, and immutable during runtime.

---

## 2. Configuration Environment Schema

| Variable Name | Type | Default Value | Valid Options / Range | Description |
| :--- | :--- | :--- | :--- | :--- |
| **Execution Environment** | | | | |
| `EXECUTION_MODE` | `str` | `simulation` | `simulation`, `paper`, `live` | Operational mode. `simulation` = historical replay; `paper` = live feed + simulated orders; `live` = real broker execution. |
| `LIVE_TRADING_ACK` | `bool` | `false` | `true`, `false` | Dual-safety interlock flag. `EXECUTION_MODE=live` will raise an immediate error if this flag is not explicitly `true`. |
| **Strategy Parameters** | | | | |
| `STRATEGY_UNDERLYING_SYMBOL` | `str` | `NSE:NIFTY50-INDEX` | Valid FYERS Index Symbol | Underlying index to monitor for opening range breakouts. |
| `STRATEGY_ORB_START_TIME` | `time` | `09:16:00` | `HH:MM:SS` (IST) | Opening time of the range formation window. |
| `STRATEGY_ORB_END_TIME` | `time` | `09:45:00` | `HH:MM:SS` (IST) | Closing time of the range formation window (trading begins immediately after). |
| `STRATEGY_MANDATORY_EXIT_TIME`| `time` | `15:10:00` | `HH:MM:SS` (IST) | Time at which all active intraday positions must be unconditionally squared off. |
| `STRATEGY_TRADING_DAYS` | `str` | `Monday,Thursday,Friday` | Comma-separated days | Days of the week on which the strategy is active. Days omitted are bypassed. |
| `STRATEGY_LOTS` | `int` | `1` | $\ge 1$ | Number of lots to buy on each breakout or reversal entry. |
| `STRATEGY_LOT_SIZE` | `int` | `25` | $\ge 1$ | Current NSE contract lot size for NIFTY options (e.g. 25, 50, or resolved dynamically). |
| `STRATEGY_PRODUCT_TYPE` | `str` | `INTRADAY` | `INTRADAY`, `MARGIN` | Product type submitted to FYERS. `INTRADAY` aligns with MIS auto-square-off products. |
| **Risk Management Parameters** | | | | |
| `MAX_DAILY_LOSS_LIMIT` | `float` | `3000.0` | Positive float (INR) | Maximum cumulative strategy loss for the session. Reaching this threshold flattens all positions and disallows further trading. |
| `PROFIT_PROTECTION_TRIGGER` | `float` | `3000.0` | Positive float (INR) | Cumulative strategy profit threshold required to activate the profit protection mechanism. |
| `PROFIT_PROTECTION_MIN_LOCK` | `float` | `500.0` | Positive float (INR) | Baseline profit floor locked once the trigger is crossed. If MTM falls to this level, positions are exited. |
| `PROFIT_TRAILING_MODE` | `str` | `lock_and_trail` | `lock_only`, `lock_and_trail` | Trailing mode corresponding to StockMock settings. |
| `PROFIT_TRAILING_STEP_PROFIT`| `float` | `1000.0` | Positive float (INR) | Step increase in cumulative MTM required to advance the trailing floor (in `lock_and_trail` mode). |
| `PROFIT_TRAILING_STEP_LOCK` | `float` | `500.0` | Positive float (INR) | Amount by which the locked profit floor increases for each completed profit step. |
| `MAX_SLIPPAGE_POINTS` | `float` | `5.0` | Positive float (Points) | Maximum acceptable execution slippage between trigger price and fill price. |
| `MAX_RETRIES_PER_ORDER` | `int` | `3` | $1 - 5$ | Maximum retry attempts for transient order placement failures before escalating. |
| **FYERS Broker Credentials** | | | | |
| `FYERS_CLIENT_ID` | `str` | `None` | String (e.g. `ABC12345-100`) | FYERS App ID generated from the FYERS developer portal. |
| `FYERS_SECRET_KEY` | `str` | `None` | String | FYERS App Secret Key. |
| `FYERS_REDIRECT_URI` | `str` | `https://127.0.0.1:5000/login` | Valid URI | Registered redirect URI matching developer portal configuration. |
| `FYERS_USER_ID` | `str` | `None` | FYERS Login ID | FYERS account username/client ID. |
| `FYERS_TOKEN_CACHE_PATH` | `Path` | `./secrets/fyers_token.json` | Valid file path | Secure local path storing cached access token and expiration metadata. |
| **Database & Persistence** | | | | |
| `DATABASE_URL` | `str` | `postgresql+asyncpg://postgres:postgres@localhost:5432/mytradeview` | PostgreSQL DSN | Primary database connection string for live and paper trading state storage. |
| `TEST_DATABASE_URL` | `str` | `sqlite+aiosqlite:///./test_local.db` | SQLite DSN | Isolated local database connection string for unit tests and local simulations. |
| `DB_POOL_SIZE` | `int` | `5` | $2 - 20$ | SQLAlchemy connection pool size. |
| `DB_MAX_OVERFLOW` | `int` | `10` | $0 - 20$ | SQLAlchemy connection pool overflow limit. |
| **Monitoring & Notifications** | | | | |
| `LOG_LEVEL` | `str` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | Structured logging verbosity level. |
| `LOG_FILE_PATH` | `Path` | `./logs/tradeview.log` | Valid file path | Destination path for rotated, structured log files. |
| `TELEGRAM_BOT_TOKEN` | `str` | `None` | Optional token | Telegram Bot API token for dispatching trade and risk alerts. |
| `TELEGRAM_CHAT_ID` | `str` | `None` | Optional chat ID | Telegram recipient chat or channel identifier. |
| `SLACK_WEBHOOK_URL` | `str` | `None` | Optional URL | Slack incoming webhook URL for alert broadcasting. |

---

## 3. Pydantic Settings Validation Implementation

The configuration is organized into modular classes under `config/`:

```python
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from datetime import time
from typing import List, Literal

class StrategySettings(BaseSettings):
    underlying_symbol: str = "NSE:NIFTY50-INDEX"
    orb_start_time: time = time(9, 16, 0)
    orb_end_time: time = time(9, 45, 0)
    mandatory_exit_time: time = time(15, 10, 0)
    trading_days: List[str] = ["Monday", "Thursday", "Friday"]
    lots: int = Field(default=1, ge=1)
    lot_size: int = Field(default=25, ge=1)
    product_type: Literal["INTRADAY", "MARGIN"] = "INTRADAY"

class RiskSettings(BaseSettings):
    max_daily_loss_limit: float = Field(default=3000.0, gt=0)
    profit_protection_trigger: float = Field(default=3000.0, gt=0)
    profit_protection_min_lock: float = Field(default=500.0, gt=0)
    profit_trailing_mode: Literal["lock_only", "lock_and_trail"] = "lock_and_trail"
    profit_trailing_step_profit: float = Field(default=1000.0, gt=0)
    profit_trailing_step_lock: float = Field(default=500.0, gt=0)
    max_slippage_points: float = Field(default=5.0, gt=0)

class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    execution_mode: Literal["simulation", "paper", "live"] = "simulation"
    live_trading_ack: bool = False
    
    strategy: StrategySettings = StrategySettings()
    risk: RiskSettings = RiskSettings()
    
    @field_validator("live_trading_ack")
    @classmethod
    def validate_live_safety(cls, v: bool, info) -> bool:
        mode = info.data.get("execution_mode")
        if mode == "live" and not v:
            raise ValueError(
                "CRITICAL SAFETY VIOLATION: Execution mode is set to 'live' but "
                "LIVE_TRADING_ACK is not set to true. Live execution aborted."
            )
        return v
```

---

## 4. Secrets Management Best Practices

1. **Zero Secret Check-ins:** The `.env` file is excluded via `.gitignore`. An `.env.example` file is maintained with placeholder values only.
2. **Access Token Refresh:** FYERS access tokens expire every 24 hours. Tokens are obtained through an explicit authorization script (`src/broker/auth.py`), serialized into `./secrets/fyers_token.json` with restricted filesystem permissions (`chmod 600`), and never logged.
3. **Structured Log Sanitization:** The logging subsystem intercepts log records and redacts fields matching regex patterns for API keys, secret hashes, access tokens, and phone numbers.

