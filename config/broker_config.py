"""Validated broker configuration for FYERS API v3."""

from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field


class BrokerConfig(BaseModel):
    """Configuration for FYERS broker integration."""
    client_id: Optional[str] = Field(default=None, description="FYERS App ID (e.g. XC12345-100)")
    secret_key: Optional[str] = Field(default=None, description="FYERS App Secret")
    redirect_uri: str = Field(default="https://127.0.0.1:5000/login", description="OAuth redirect URI")
    user_id: Optional[str] = Field(default=None, description="FYERS login user ID")
    token_cache_path: Path = Field(
        default=Path("./secrets/fyers_token.json"),
        description="Path to stored daily OAuth access token"
    )
    request_timeout_seconds: float = Field(default=10.0, gt=0, description="REST API HTTP timeout")
    max_connection_retries: int = Field(default=3, ge=1, le=10, description="Max reconnection retries")
    reconciliation_interval_seconds: float = Field(
        default=5.0,
        ge=1.0,
        le=60.0,
        description="Interval between broker position reconciliation checks"
    )

