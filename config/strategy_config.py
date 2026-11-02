"""Validated strategy configuration schema."""

from datetime import time
from typing import List
from pydantic import BaseModel, Field, field_validator
from src.common.constants import ProductType, TradingDay


class StrategyConfig(BaseModel):
    """Configuration parameters for the NIFTY ORB strategy."""
    underlying_symbol: str = Field(
        default="NSE:NIFTY50-INDEX",
        description="Underlying index symbol on FYERS"
    )
    orb_start_time: time = Field(
        default=time(9, 16, 0),
        description="Opening Range start time (IST)"
    )
    orb_end_time: time = Field(
        default=time(9, 45, 0),
        description="Opening Range end time (IST)"
    )
    mandatory_exit_time: time = Field(
        default=time(15, 10, 0),
        description="Intraday mandatory square-off time (IST)"
    )
    trading_days: List[str] = Field(
        default_factory=lambda: [TradingDay.MONDAY.value, TradingDay.THURSDAY.value, TradingDay.FRIDAY.value],
        description="Allowed trading days"
    )
    lots: int = Field(default=1, ge=1, description="Number of lots per trade")
    lot_size: int = Field(default=25, ge=1, description="Contract lot size for NIFTY")
    product_type: ProductType = Field(default=ProductType.INTRADAY, description="Order product type")

    @field_validator("orb_end_time")
    @classmethod
    def validate_orb_window(cls, v: time, info) -> time:
        start = info.data.get("orb_start_time", time(9, 16, 0))
        if v <= start:
            raise ValueError(f"orb_end_time ({v}) must be strictly after orb_start_time ({start})")
        return v

    @field_validator("mandatory_exit_time")
    @classmethod
    def validate_exit_time(cls, v: time, info) -> time:
        orb_end = info.data.get("orb_end_time", time(9, 45, 0))
        if v <= orb_end:
            raise ValueError(f"mandatory_exit_time ({v}) must be strictly after orb_end_time ({orb_end})")
        if v > time(15, 25, 0):
            raise ValueError(f"mandatory_exit_time ({v}) must be before 15:25:00 IST to prevent broker square-off")
        return v

    @field_validator("trading_days")
    @classmethod
    def validate_trading_days(cls, v: List[str]) -> List[str]:
        valid_days = {d.value for d in TradingDay}
        for day in v:
            if day not in valid_days:
                raise ValueError(f"Invalid trading day '{day}'. Must be one of {valid_days}")
        return v
