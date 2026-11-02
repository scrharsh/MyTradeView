"""Validated risk management and circuit breaker configuration."""

from typing import Literal
from pydantic import BaseModel, Field, field_validator


class RiskConfig(BaseModel):
    """Hard risk management limits and profit protection settings."""
    max_daily_loss_limit: float = Field(
        default=3000.0,
        gt=0,
        description="Daily strategy MTM loss limit in INR triggering emergency shutdown"
    )
    profit_protection_trigger: float = Field(
        default=3000.0,
        gt=0,
        description="Strategy MTM gain threshold activating profit protection floor in INR"
    )
    profit_protection_min_lock: float = Field(
        default=500.0,
        gt=0,
        description="Baseline minimum profit floor in INR locked once trigger is crossed"
    )
    profit_trailing_mode: Literal["lock_only", "lock_and_trail"] = Field(
        default="lock_and_trail",
        description="Profit trailing mode matching StockMock"
    )
    profit_trailing_step_profit: float = Field(
        default=1000.0,
        gt=0,
        description="Incremental profit step in INR to advance the trailing floor"
    )
    profit_trailing_step_lock: float = Field(
        default=500.0,
        gt=0,
        description="Increment in INR added to the floor per completed profit step"
    )
    max_slippage_points: float = Field(
        default=5.0,
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
    def validate_min_lock_less_than_trigger(cls, v: float, info) -> float:
        trigger = info.data.get("profit_protection_trigger", 3000.0)
        if v >= trigger:
            raise ValueError(f"profit_protection_min_lock ({v}) must be strictly less than trigger ({trigger})")
        return v
