"""Domain models and status mappings for the Order Management System (OMS)."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field

from src.common.constants import OrderSide, OrderStatus, OrderType, ProductType
from src.common.time_utils import now_utc
from src.core.models import Fill


class OmsOrderStatus(str, Enum):
    """
    Granular internal order lifecycle statuses for MyTradeView OMS.
    Covers the full progression from strategy intent to broker finality and reconciliation.
    """
    INTENT_CREATED = "INTENT_CREATED"
    VALIDATING = "VALIDATING"
    SUBMISSION_PENDING = "SUBMISSION_PENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


def map_fyers_status_to_oms(fyers_status: OrderStatus, filled_qty: int, qty: int) -> OmsOrderStatus:
    """
    Map official FYERS API v3 integer status codes into granular internal OMS status.
    FYERS does not have a distinct code for partial fill (it remains 6 PENDING with filledQty > 0).
    """
    if fyers_status == OrderStatus.FILLED:
        return OmsOrderStatus.FILLED
    elif fyers_status == OrderStatus.CANCELLED:
        return OmsOrderStatus.CANCELLED
    elif fyers_status == OrderStatus.REJECTED:
        return OmsOrderStatus.REJECTED
    elif fyers_status == OrderStatus.TRANSIT:
        return OmsOrderStatus.SUBMISSION_PENDING
    elif fyers_status == OrderStatus.PENDING:
        if 0 < filled_qty < qty:
            return OmsOrderStatus.PARTIALLY_FILLED
        return OmsOrderStatus.OPEN
    elif fyers_status == OrderStatus.EXPIRED:
        return OmsOrderStatus.CANCELLED
    return OmsOrderStatus.RECONCILIATION_REQUIRED


def map_oms_status_to_fyers(oms_status: OmsOrderStatus) -> OrderStatus:
    """Map internal OMS status into equivalent FYERS API v3 integer status code."""
    if oms_status == OmsOrderStatus.FILLED:
        return OrderStatus.FILLED
    elif oms_status in {OmsOrderStatus.CANCELLED, OmsOrderStatus.CANCELLATION_REQUESTED}:
        return OrderStatus.CANCELLED
    elif oms_status in {OmsOrderStatus.REJECTED, OmsOrderStatus.FAILED}:
        return OrderStatus.REJECTED
    elif oms_status in {OmsOrderStatus.SUBMISSION_PENDING, OmsOrderStatus.INTENT_CREATED, OmsOrderStatus.VALIDATING}:
        return OrderStatus.TRANSIT
    elif oms_status in {OmsOrderStatus.ACKNOWLEDGED, OmsOrderStatus.OPEN, OmsOrderStatus.PARTIALLY_FILLED}:
        return OrderStatus.PENDING
    return OrderStatus.PENDING


class OrderIntent(BaseModel):
    """
    Immutable representation of an order intent emitted by a strategy or risk engine
    prior to validation and broker submission.
    """
    model_config = ConfigDict(frozen=True)

    intent_id: str = Field(default_factory=lambda: f"INT_{uuid4().hex[:12]}")
    client_order_id: str
    session_id: str
    strategy_name: str
    strategy_version: int = 1
    symbol: str
    side: OrderSide
    order_type: OrderType
    qty: int = Field(gt=0)
    product_type: ProductType = ProductType.INTRADAY
    limit_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    stop_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    tag: str = Field(max_length=20, default="")
    reason: str = ""
    created_at: datetime = Field(default_factory=now_utc)


class OmsOrderStateTransition(BaseModel):
    """Audit record of a state transition in the OMS lifecycle."""
    model_config = ConfigDict(frozen=True)

    transition_id: str = Field(default_factory=lambda: f"OTR_{uuid4().hex[:10]}")
    client_order_id: str
    from_status: OmsOrderStatus
    to_status: OmsOrderStatus
    filled_qty: int
    avg_price: Decimal
    message: str = ""
    timestamp: datetime = Field(default_factory=now_utc)


class OmsOrder(BaseModel):
    """
    Authoritative stateful order model managed by the Order Management System.
    """
    client_order_id: str
    broker_order_id: Optional[str] = None
    session_id: str
    intent_id: str
    strategy_name: str
    strategy_version: int = 1
    symbol: str
    side: OrderSide
    order_type: OrderType
    product_type: ProductType
    qty: int
    filled_qty: int = 0
    remaining_qty: int
    oms_status: OmsOrderStatus = OmsOrderStatus.INTENT_CREATED
    avg_fill_price: Decimal = Decimal("0.00")
    limit_price: Decimal = Decimal("0.00")
    tag: str
    status_message: str = ""
    fills: List[Fill] = Field(default_factory=list)
    transitions: List[OmsOrderStateTransition] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
