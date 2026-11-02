"""Root application configuration with environment loading and safety validators."""

from pathlib import Path
from typing import Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from src.common.constants import ExecutionMode
from config.strategy_config import StrategyConfig
from config.broker_config import BrokerConfig
from config.risk_config import RiskConfig


class AppConfig(BaseSettings):
    """Unified application settings with fail-safe validation."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_nested_delimiter="__"
    )

    execution_mode: ExecutionMode = Field(
        default=ExecutionMode.SIMULATION,
        description="Execution mode: simulation | paper | live"
    )
    live_trading_ack: bool = Field(
        default=False,
        description="Explicit user acknowledgement required for live trading"
    )

    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)

    # Persistence
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/mytradeview",
        description="Production database DSN (PostgreSQL)"
    )
    test_database_url: str = Field(
        default="sqlite+aiosqlite:///./test_local.db",
        description="Isolated test database DSN (SQLite)"
    )
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=10, ge=0, le=50)

    # Logging & Alerts
    log_level: str = Field(default="INFO")
    log_file_path: Path = Field(default=Path("./logs/tradeview.log"))
    telegram_bot_token: Optional[str] = Field(default=None)
    telegram_chat_id: Optional[str] = Field(default=None)
    slack_webhook_url: Optional[str] = Field(default=None)

    @field_validator("live_trading_ack")
    @classmethod
    def validate_live_mode_safety(cls, v: bool, info) -> bool:
        mode = info.data.get("execution_mode")
        if mode == ExecutionMode.LIVE and not v:
            raise ValueError(
                "CRITICAL SAFETY VIOLATION: ExecutionMode is set to 'live' but "
                "live_trading_ack is False. Live execution blocked."
            )
        return v


def get_config() -> AppConfig:
    """Helper to instantiate validated configuration."""
    return AppConfig()
