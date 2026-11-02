"""Unit tests for configuration validation and fail-safe checks."""

from datetime import time
import pytest
from pydantic import ValidationError

from config.base import AppConfig
from config.strategy_config import StrategyConfig
from config.risk_config import RiskConfig
from src.common.constants import ExecutionMode


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


def test_strategy_config_invalid_time_window():
    """Verify error raised when orb_end_time <= orb_start_time."""
    with pytest.raises(ValidationError):
        StrategyConfig(
            orb_start_time=time(9, 45, 0),
            orb_end_time=time(9, 16, 0),
        )


def test_risk_config_defaults():
    """Verify default risk parameters."""
    cfg = RiskConfig()
    assert cfg.max_daily_loss_limit == 3000.0
    assert cfg.profit_protection_trigger == 3000.0
    assert cfg.profit_protection_min_lock == 500.0


def test_risk_config_invalid_min_lock():
    """Verify error when min_lock >= trigger."""
    with pytest.raises(ValidationError):
        RiskConfig(
            profit_protection_trigger=2000.0,
            profit_protection_min_lock=2500.0,
        )


def test_app_config_live_safety_interlock():
    """Verify live trading requires explicit LIVE_TRADING_ACK=true."""
    # Simulation mode succeeds by default without ack
    app_sim = AppConfig(execution_mode=ExecutionMode.SIMULATION, live_trading_ack=False)
    assert app_sim.execution_mode == ExecutionMode.SIMULATION

    # Live mode with live_trading_ack=False must fail!
    with pytest.raises(ValidationError) as exc_info:
        AppConfig(execution_mode=ExecutionMode.LIVE, live_trading_ack=False)
    assert "CRITICAL SAFETY VIOLATION" in str(exc_info.value)

    # Live mode with live_trading_ack=True is allowed
    app_live = AppConfig(execution_mode=ExecutionMode.LIVE, live_trading_ack=True)
    assert app_live.execution_mode == ExecutionMode.LIVE
