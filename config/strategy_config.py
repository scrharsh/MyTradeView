"""Validated strategy configuration schema with fail-closed checks for unresolved decisions."""

from datetime import time
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

from src.common.constants import (
    BreakoutConfirmationMode,
    ExpiryDaySelectionMode,
    OrderExecutionStyle,
    ProductType,
    ProfitTrailingMode,
    RangeBoundaryMode,
    TradingDay,
)
from src.common.exceptions import UnconfirmedStrategySettingError


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

    # --------------------------------------------------------------------------
    # Explicitly Unresolved Strategy Semantics (Awaiting User Confirmation)
    # Default to None so they fail closed if execution is attempted without confirmation.
    # --------------------------------------------------------------------------
    breakout_confirmation_mode: Optional[BreakoutConfirmationMode] = Field(
        default=None,
        description="UNRESOLVED: Candle-close vs intrabar tick breakout trigger"
    )
    range_boundary_mode: Optional[RangeBoundaryMode] = Field(
        default=RangeBoundaryMode.UP_TO_0945_CLOSE,
        description="Active boundary: include completed 09:45 candle (30 bars)"
    )
    order_execution_style: Optional[OrderExecutionStyle] = Field(
        default=None,
        description="UNRESOLVED: Market vs Marketable-limit option entry"
    )
    expiry_day_selection: Optional[ExpiryDaySelectionMode] = Field(
        default=None,
        description="UNRESOLVED: 0 DTE vs Next-weekly contract selection on expiry day"
    )

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
        normalized: List[str] = []
        for day in v:
            clean = day.strip().capitalize()
            if clean not in valid_days:
                raise ValueError(f"Invalid trading day '{day}'. Must be one of {valid_days}")
            normalized.append(clean)
        return normalized

    def validate_executable_for_trading(self) -> None:
        """
        Fail-closed check: Ensures no unresolved configuration decision is left unconfirmed
        before strategy signals or live/paper executions can proceed.
        """
        if self.breakout_confirmation_mode is None:
            raise UnconfirmedStrategySettingError(
                "breakout_confirmation_mode",
                "Select CANDLE_CLOSE (1-min bar close) or INTRABAR_TICK (touch breach)"
            )
        if self.range_boundary_mode is None:
            raise UnconfirmedStrategySettingError(
                "range_boundary_mode",
                "Select UP_TO_0945_CLOSE (active: 30 bars, ends at 09:46:00) or UP_TO_0944_CLOSE (legacy: 29 bars)"
            )
        if self.order_execution_style is None:
            raise UnconfirmedStrategySettingError(
                "order_execution_style",
                "Select MARKET or MARKETABLE_LIMIT execution style"
            )
        if self.expiry_day_selection is None:
            raise UnconfirmedStrategySettingError(
                "expiry_day_selection",
                "Select CURRENT_WEEKLY_0DTE or NEXT_WEEKLY contract selection on expiry day"
            )
