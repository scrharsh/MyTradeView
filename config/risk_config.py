"""Validated risk management and circuit breaker configuration with Decimal precision."""

from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, field_validator

from src.common.constants import ProfitTrailingMode
from src.common.exceptions import UnconfirmedStrategySettingError


class RiskConfig(BaseModel):
    """Hard risk management limits and profit protection settings using fixed precision."""
    max_daily_loss_limit: Decimal = Field(
        default=Decimal("3000.00"),
        gt=0,
        description="Daily strategy MTM loss limit in INR triggering emergency shutdown"
    )
    profit_protection_trigger: Decimal = Field(
        default=Decimal("3000.00"),
        gt=0,
        description="Strategy MTM gain threshold activating profit protection floor in INR"
    )
    profit_protection_min_lock: Decimal = Field(
        default=Decimal("500.00"),
        gt=0,
        description="Baseline minimum profit floor in INR locked once trigger is crossed"
    )

    # --------------------------------------------------------------------------
    # Explicitly Unresolved Profit Protection Trailing Semantics
    # --------------------------------------------------------------------------
    profit_trailing_mode: Optional[ProfitTrailingMode] = Field(
        default=None,
        description="UNRESOLVED: Lock-only vs Lock-and-trail mechanics"
    )
    profit_trailing_step_profit: Decimal = Field(
        default=Decimal("1000.00"),
        gt=0,
        description="Incremental profit step in INR to advance the trailing floor (if lock_and_trail)"
    )
    profit_trailing_step_lock: Decimal = Field(
        default=Decimal("500.00"),
        gt=0,
        description="Increment in INR added to the floor per completed profit step (if lock_and_trail)"
    )

    max_slippage_points: Decimal = Field(
        default=Decimal("5.00"),
        gt=0,
        description="Maximum tolerated option execution slippage in points"
    )
    max_retries_per_order: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Maximum attempts to place an order before raising a critical risk alert"
    )

    @field_validator("profit_protection_min_lock")
    @classmethod
    def validate_min_lock_less_than_trigger(cls, v: Decimal, info) -> Decimal:
        trigger = info.data.get("profit_protection_trigger", Decimal("3000.00"))
        if v >= trigger:
            raise ValueError(f"profit_protection_min_lock ({v}) must be strictly less than trigger ({trigger})")
        return v

    def validate_executable_for_trading(self) -> None:
        """Fail-closed check: Ensures profit-protection trailing semantics are explicitly chosen."""
        if self.profit_trailing_mode is None:
            raise UnconfirmedStrategySettingError(
                "profit_trailing_mode",
                "Select LOCK_MINIMUM_ONLY (fixed ₹500 floor) or LOCK_AND_TRAIL (step-based trailing floor)"
            )
