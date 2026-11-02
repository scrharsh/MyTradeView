"""Domain models, payload schemas, and normalization structures for broker adapters."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from src.common.constants import OrderSide, OrderStatus, OrderType, ProductType
from src.common.time_utils import now_utc


class BrokerConnectionStatus(str, Enum):
    """Connection health state of broker REST API and sessions."""
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    ERROR = "ERROR"


class AuthStatus(BaseModel):
    """
    Authoritative authentication status for broker integration.
    Redacts tokens and keys to prevent credential leakage.
    """
    model_config = ConfigDict(frozen=True)

    is_authenticated: bool
    app_id: str
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    email: Optional[str] = None
    access_token: Optional[str] = None
    token_expiry: Optional[datetime] = None
    message: str = "Authenticated successfully"
    validated_at: datetime = Field(default_factory=now_utc)

    @property
    def broker_user_id(self) -> Optional[str]:
        return self.user_id

    @property
    def display_name(self) -> Optional[str]:
        return self.user_name

    def __repr__(self) -> str:
        masked_app_id = self.app_id[:4] + "***" if len(self.app_id) > 4 else "***"
        masked_tok = (self.access_token[:6] + "...redacted") if self.access_token else "None"
        return (
            f"AuthStatus(is_authenticated={self.is_authenticated}, "
            f"app_id='{masked_app_id}', user_id='{self.user_id}', access_token='{masked_tok}', "
            f"message='{self.message}')"
        )


class BrokerOrderPayload(BaseModel):
    """
    Normalized outbound order placement payload matching FYERS API v3 /api/v3/orders/sync.
    """
    model_config = ConfigDict(frozen=True)

    symbol: str
    qty: int = Field(gt=0)
    type: OrderType
    side: OrderSide
    product_type: ProductType = ProductType.INTRADAY
    limit_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    stop_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    disclosed_qty: int = Field(default=0, ge=0)
    validity: str = "DAY"
    offline_order: bool = False
    tag: str = Field(max_length=20, default="")
    client_order_id: str

    def to_fyers_dict(self) -> Dict[str, Any]:
        """Convert to official FYERS API v3 payload schema."""
        return {
            "symbol": self.symbol,
            "qty": self.qty,
            "type": self.type.value,
            "side": self.side.value,
            "productType": self.product_type.value,
            "limitPrice": float(self.limit_price),
            "stopPrice": float(self.stop_price),
            "disclosedQty": self.disclosed_qty,
            "validity": self.validity,
            "offlineOrder": self.offline_order,
            "tag": self.tag,
        }


class BrokerOrderResponse(BaseModel):
    """
    Normalized response from broker order submission, modification, or cancellation.
    """
    model_config = ConfigDict(frozen=True)

    success: bool
    broker_order_id: Optional[str] = None
    client_order_id: str
    status_code: int
    message: str
    is_timeout_ambiguous: bool = False
    raw_response: Dict[str, Any] = Field(default_factory=dict)


class BrokerOrderBookItem(BaseModel):
    """Normalized order item returned from broker order-book query."""
    model_config = ConfigDict(frozen=True)

    broker_order_id: str
    client_order_id: Optional[str] = None
    symbol: str
    qty: int
    filled_qty: int
    remaining_qty: int
    status: OrderStatus
    order_type: OrderType
    side: OrderSide
    avg_price: Decimal
    limit_price: Decimal
    order_timestamp: datetime
    message: str = ""
    tag: Optional[str] = None


class BrokerTradeItem(BaseModel):
    """Normalized trade item returned from broker trade-book query."""
    model_config = ConfigDict(frozen=True)

    trade_id: str
    broker_order_id: str
    symbol: str
    side: OrderSide
    traded_qty: int
    traded_price: Decimal
    trade_time: datetime
    product_type: str = "INTRADAY"


class BrokerPosition(BaseModel):
    """Normalized position returned from broker positions endpoint."""
    model_config = ConfigDict(frozen=True)

    symbol: str
    net_qty: int
    buy_qty: int
    sell_qty: int
    buy_avg: Decimal
    sell_avg: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    product_type: str = "INTRADAY"


class BrokerFunds(BaseModel):
    """Normalized fund and margin availability."""
    model_config = ConfigDict(frozen=True)

    total_balance: Decimal
    available_balance: Decimal
    used_margin: Decimal
    currency: str = "INR"
    timestamp: datetime = Field(default_factory=now_utc)


# ------------------------------------------------------------------------------
# Security & Token Redaction Utilities
# ------------------------------------------------------------------------------

SENSITIVE_KEYS = {
    "access_token",
    "auth_token",
    "token",
    "secret_key",
    "app_secret",
    "client_secret",
    "password",
    "pin",
    "authorization",
    "bearer",
}

TOKEN_PATTERN = re.compile(r"(eyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+)")


def redact_sensitive_dict(data: Any) -> Any:
    """
    Recursively redact sensitive authentication tokens, passwords, and secrets
    from dictionaries, lists, and strings for safe logging and telemetry.
    """
    if isinstance(data, dict):
        redacted = {}
        for k, v in data.items():
            if k.lower() in SENSITIVE_KEYS or any(s in k.lower() for s in ("secret", "token", "password", "key")):
                redacted[k] = "[REDACTED]"
            else:
                redacted[k] = redact_sensitive_dict(v)
        return redacted
    elif isinstance(data, list):
        return [redact_sensitive_dict(item) for item in data]
    elif isinstance(data, str):
        # Match potential JWT tokens and redact
        return TOKEN_PATTERN.sub("[REDACTED_JWT]", data)
    return data
