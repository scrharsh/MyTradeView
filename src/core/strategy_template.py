"""Strategy Template definitions, parameter validation, and versioned configuration management.

Enables the workstation vision where strategies are configuration-driven templates
rather than hardcoded bots.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.common.constants import (
    BreakoutConfirmationMode,
    ExpiryDaySelectionMode,
    OrderExecutionStyle,
    OrderType,
    ProfitTrailingMode,
    RangeBoundaryMode,
    StrategyTemplateType,
    TradingDay,
)
from src.common.exceptions import ConfigurationError, UnconfirmedStrategySettingError
from src.common.time_utils import now_utc


class StrategyParameterDefinition(BaseModel):
    """Specification of an allowed parameter in a strategy template."""
    model_config = ConfigDict(frozen=True)

    name: str
    param_type: str  # str, int, float, Decimal, bool, time, list
    default_value: Any
    description: str
    is_required: bool = True
    allowed_values: Optional[List[Any]] = None


class StrategyTemplate(BaseModel):
    """
    Template blueprint defining supported parameters, assets, and order types for a strategy family.
    """
    model_config = ConfigDict(frozen=True)

    template_type: StrategyTemplateType
    display_name: str
    description: str
    supported_underlyings: List[str]
    supported_order_types: List[OrderType]
    parameters: Dict[str, StrategyParameterDefinition]

    def validate_parameters(self, config_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate user-supplied parameters against template specification.
        Detects unknown/unsupported parameters, missing required parameters, and invalid values.
        """
        validated: Dict[str, Any] = {}
        supported_keys = set(self.parameters.keys())
        supplied_keys = set(config_params.keys())

        # Check for unsupported extraneous parameters
        unsupported = supplied_keys - supported_keys
        if unsupported:
            raise ConfigurationError(
                f"Unsupported parameter(s) for template '{self.template_type.value}': {sorted(list(unsupported))}. "
                f"Supported parameters are: {sorted(list(supported_keys))}"
            )

        # Validate each parameter
        for param_name, param_def in self.parameters.items():
            if param_name in config_params:
                val = config_params[param_name]
            elif param_def.is_required and param_def.default_value is None:
                raise ConfigurationError(
                    f"Missing required parameter '{param_name}' for template '{self.template_type.value}'"
                )
            else:
                val = param_def.default_value

            # Check allowed values if specified
            if param_def.allowed_values is not None and val is not None:
                if isinstance(val, list):
                    for item in val:
                        if item not in param_def.allowed_values:
                            raise ConfigurationError(
                                f"Invalid value '{item}' in list for parameter '{param_name}'. "
                                f"Allowed values: {param_def.allowed_values}"
                            )
                elif val not in param_def.allowed_values:
                    raise ConfigurationError(
                        f"Invalid value '{val}' for parameter '{param_name}'. "
                        f"Allowed values: {param_def.allowed_values}"
                    )

            validated[param_name] = val

        return validated


class VersionedStrategyConfig(BaseModel):
    """
    Persistent, versioned strategy instance configuration.
    
    Immutable snapshot tied to backtests and live/paper trading sessions.
    """
    model_config = ConfigDict(frozen=True)

    config_id: str = Field(default_factory=lambda: f"STRAT_{uuid4().hex[:12]}")
    name: str = Field(description="User-friendly name of the strategy configuration")
    template_type: StrategyTemplateType = StrategyTemplateType.NIFTY_ORB
    version: int = Field(default=1, ge=1)
    underlying_symbol: str = "NSE:NIFTY50-INDEX"
    parameters: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    def create_next_version(self, updated_parameters: Dict[str, Any], name: Optional[str] = None) -> "VersionedStrategyConfig":
        """Generate a new version increment of this configuration."""
        merged_params = dict(self.parameters)
        merged_params.update(updated_parameters)
        return VersionedStrategyConfig(
            config_id=f"STRAT_{uuid4().hex[:12]}",
            name=name or self.name,
            template_type=self.template_type,
            version=self.version + 1,
            underlying_symbol=self.underlying_symbol,
            parameters=merged_params,
            is_active=True,
            created_at=now_utc(),
            updated_at=now_utc(),
        )


# ------------------------------------------------------------------------------
# Built-in Template Registry
# ------------------------------------------------------------------------------

def get_nifty_orb_template() -> StrategyTemplate:
    """Return authoritative StrategyTemplate specification for NIFTY ORB."""
    return StrategyTemplate(
        template_type=StrategyTemplateType.NIFTY_ORB,
        display_name="NIFTY Opening Range Breakout (ORB)",
        description="Breaks out of the 09:16-09:45 IST opening range with ATM weekly options and atomic reversals.",
        supported_underlyings=["NSE:NIFTY50-INDEX"],
        supported_order_types=[OrderType.MARKET, OrderType.LIMIT],
        parameters={
            "underlying_symbol": StrategyParameterDefinition(
                name="underlying_symbol",
                param_type="str",
                default_value="NSE:NIFTY50-INDEX",
                description="Underlying index symbol",
                allowed_values=["NSE:NIFTY50-INDEX"],
            ),
            "orb_start_time": StrategyParameterDefinition(
                name="orb_start_time",
                param_type="str",
                default_value="09:16:00",
                description="Opening range start time in HH:MM:SS IST",
            ),
            "orb_end_time": StrategyParameterDefinition(
                name="orb_end_time",
                param_type="str",
                default_value="09:45:00",
                description="Opening range end time in HH:MM:SS IST",
            ),
            "mandatory_exit_time": StrategyParameterDefinition(
                name="mandatory_exit_time",
                param_type="str",
                default_value="15:10:00",
                description="Mandatory intraday square-off time in HH:MM:SS IST",
            ),
            "trading_days": StrategyParameterDefinition(
                name="trading_days",
                param_type="list",
                default_value=["Monday", "Thursday", "Friday"],
                description="Allowed trading days",
                allowed_values=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
            ),
            "lots": StrategyParameterDefinition(
                name="lots",
                param_type="int",
                default_value=1,
                description="Number of lots per trade",
            ),
            "breakout_confirmation_mode": StrategyParameterDefinition(
                name="breakout_confirmation_mode",
                param_type="str",
                default_value=BreakoutConfirmationMode.CANDLE_CLOSE.value,
                description="Breakout confirmation mode",
                allowed_values=[m.value for m in BreakoutConfirmationMode],
            ),
            "range_boundary_mode": StrategyParameterDefinition(
                name="range_boundary_mode",
                param_type="str",
                default_value=RangeBoundaryMode.UP_TO_0945_CLOSE.value,
                description="Opening range candle boundary window mode",
                allowed_values=[m.value for m in RangeBoundaryMode],
            ),
            "expiry_day_selection": StrategyParameterDefinition(
                name="expiry_day_selection",
                param_type="str",
                default_value=ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE.value,
                description="Expiry contract selection mode on expiry days",
                allowed_values=[m.value for m in ExpiryDaySelectionMode],
            ),
            "max_daily_loss_limit": StrategyParameterDefinition(
                name="max_daily_loss_limit",
                param_type="Decimal",
                default_value=Decimal("3000.00"),
                description="Daily strategy MTM loss threshold in INR",
            ),
            "profit_protection_trigger": StrategyParameterDefinition(
                name="profit_protection_trigger",
                param_type="Decimal",
                default_value=Decimal("3000.00"),
                description="Profit protection activation threshold in INR",
            ),
            "profit_protection_min_lock": StrategyParameterDefinition(
                name="profit_protection_min_lock",
                param_type="Decimal",
                default_value=Decimal("500.00"),
                description="Baseline minimum profit lock in INR",
            ),
            "profit_trailing_mode": StrategyParameterDefinition(
                name="profit_trailing_mode",
                param_type="str",
                default_value=ProfitTrailingMode.LOCK_MINIMUM_ONLY.value,
                description="Profit protection trailing mode (verified baseline: lock_minimum_only at ₹500 floor once ₹3,000 trigger is crossed)",
                allowed_values=[m.value for m in ProfitTrailingMode],
            ),
            "profit_trailing_step_profit": StrategyParameterDefinition(
                name="profit_trailing_step_profit",
                param_type="Decimal",
                default_value=Decimal("1000.00"),
                description="Step profit increment to trail floor in INR",
            ),
            "profit_trailing_step_lock": StrategyParameterDefinition(
                name="profit_trailing_step_lock",
                param_type="Decimal",
                default_value=Decimal("500.00"),
                description="Step floor lock increment in INR",
            ),
        },
    )


STRATEGY_TEMPLATES: Dict[StrategyTemplateType, StrategyTemplate] = {
    StrategyTemplateType.NIFTY_ORB: get_nifty_orb_template(),
}


def get_strategy_template(template_type: StrategyTemplateType) -> StrategyTemplate:
    """Retrieve strategy template by type, or raise ConfigurationError."""
    template = STRATEGY_TEMPLATES.get(template_type)
    if template is None:
        raise ConfigurationError(f"Strategy template '{template_type}' is not registered.")
    return template

