"""Abstract interfaces and domain models for the dedicated Risk Management Layer."""

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from src.common.constants import RiskRejectReason, RiskTriggerReason
from src.common.time_utils import now_utc
from src.core.models import BrokerOrder, Fill, OptionContract, OrderRequest, Position


class PreTradeValidationResult(BaseModel):
    """
    Structured outcome of pre-trade risk evaluation.
    Provides clear auditability for order acceptance or rejection.
    """
    model_config = ConfigDict(frozen=True)

    is_valid: bool
    reason_code: Optional[RiskRejectReason] = None
    message: str = "Order validated successfully."
    context: Dict[str, Any] = Field(default_factory=dict)
    evaluated_at: datetime = Field(default_factory=now_utc)


class RiskSnapshot(BaseModel):
    """
    Immutable snapshot of risk metrics and mark-to-market status.
    """
    model_config = ConfigDict(frozen=True)

    session_id: str
    timestamp: datetime = Field(default_factory=now_utc)
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_mtm: Decimal
    peak_mtm: Decimal
    current_drawdown: Decimal
    is_kill_switch_active: bool
    kill_switch_reason: Optional[RiskTriggerReason] = None
    is_profit_protection_active: bool
    profit_floor: Decimal
    open_positions_count: int
    is_data_stale: bool = False


class BaseMtmTracker(ABC):
    """Abstract interface for Mark-to-Market tracking."""

    @abstractmethod
    def update_position(self, position: Position, current_ltp: Optional[Decimal] = None) -> RiskSnapshot:
        """Update tracker with position state."""
        pass

    @abstractmethod
    def on_fill(self, fill: Fill) -> None:
        """Ingest execution fill report."""
        pass

    @property
    @abstractmethod
    def total_mtm(self) -> Decimal:
        """Total strategy MTM (realized + unrealized)."""
        pass
