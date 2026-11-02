"""Unit tests for configuration validation, fail-safe checks, and fail-closed behavior."""

from datetime import time
from decimal import Decimal
import pytest
from pydantic import ValidationError

from config.base import AppConfig
from config.broker_config import BrokerConfig
from config.risk_config import RiskConfig
from config.strategy_config import StrategyConfig
from src.common.constants import (
    BreakoutConfirmationMode,
    ExecutionMode,
    ExpiryDaySelectionMode,
    OrderExecutionStyle,
    ProfitTrailingMode,
    RangeBoundaryMode,
)
from src.common.exceptions import UnconfirmedStrategySettingError


def test_default_execution_mode_is_paper():
    """Verify that execution mode defaults strictly to PAPER for safety."""
    app_config = AppConfig()
    assert app_config.execution_mode == ExecutionMode.PAPER
    assert app_config.live_trading_ack is False


def test_live_execution_cannot_be_activated_accidentally():
    """Verify that LIVE mode fails closed unless explicitly acknowledged."""
    # Attempting LIVE mode without acknowledgement must fail immediately
    with pytest.raises(ValidationError) as exc_info:
        AppConfig(execution_mode=ExecutionMode.LIVE, live_trading_ack=False)
    assert "CRITICAL SAFETY VIOLATION" in str(exc_info.value)

    # Valid explicit activation
    app_live = AppConfig(execution_mode=ExecutionMode.LIVE, live_trading_ack=True)
    assert app_live.execution_mode == ExecutionMode.LIVE


def test_strategy_config_defaults():
    """Verify default strategy configuration adheres to requirements."""
    cfg = StrategyConfig()
    assert cfg.underlying_symbol == "NSE:NIFTY50-INDEX"
    assert cfg.orb_start_time == time(9, 16, 0)
    assert cfg.orb_end_time == time(9, 45, 0)
    assert cfg.mandatory_exit_time == time(15, 10, 0)
    assert cfg.lots == 1
    assert cfg.lot_size == 25
    assert "Monday" in cfg.trading_days
    assert "Thursday" in cfg.trading_days
    assert "Friday" in cfg.trading_days
    assert "Tuesday" not in cfg.trading_days
    assert "Wednesday" not in cfg.trading_days


def test_risk_config_decimal_defaults():
    """Verify default risk parameters use exact Decimal precision."""
    cfg = RiskConfig()
    assert cfg.max_daily_loss_limit == Decimal("3000.00")
    assert cfg.profit_protection_trigger == Decimal("3000.00")
    assert cfg.profit_protection_min_lock == Decimal("500.00")
    assert isinstance(cfg.max_daily_loss_limit, Decimal)


def test_risk_config_invalid_min_lock():
    """Verify error when min_lock >= trigger."""
    with pytest.raises(ValidationError):
        RiskConfig(
            profit_protection_trigger=Decimal("2000.00"),
            profit_protection_min_lock=Decimal("2500.00"),
        )


def test_strategy_config_invalid_time_window():
    """Verify error raised when orb_end_time <= orb_start_time."""
    with pytest.raises(ValidationError):
        StrategyConfig(
            orb_start_time=time(9, 45, 0),
            orb_end_time=time(9, 16, 0),
        )


def test_fail_closed_on_unresolved_strategy_decisions():
    """Verify system fails closed when strategy execution is attempted with unconfirmed settings."""
    app_config = AppConfig()

    # By default, all 5 strategy decisions are unresolved (None)
    with pytest.raises(UnconfirmedStrategySettingError) as exc_info:
        app_config.validate_executable_for_trading()
    assert "FAIL-CLOSED SAFETY INTERLOCK" in str(exc_info.value)
    assert "breakout_confirmation_mode" in str(exc_info.value)

    # Resolve breakout confirmation mode only -> Next unresolved decision must block execution
    app_config.strategy.breakout_confirmation_mode = BreakoutConfirmationMode.CANDLE_CLOSE
    with pytest.raises(UnconfirmedStrategySettingError) as exc_info:
        app_config.validate_executable_for_trading()
    assert "range_boundary_mode" in str(exc_info.value)

    # Resolve all remaining unresolved decisions
    app_config.strategy.range_boundary_mode = RangeBoundaryMode.UP_TO_0944_CLOSE
    app_config.strategy.order_execution_style = OrderExecutionStyle.MARKET
    app_config.strategy.expiry_day_selection = ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE
    app_config.risk.profit_trailing_mode = ProfitTrailingMode.LOCK_AND_TRAIL

    # Now validation succeeds
    app_config.validate_executable_for_trading()
