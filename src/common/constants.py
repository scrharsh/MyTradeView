"""Domain enumerations, constants, and valid state transition graphs."""

from enum import Enum, IntEnum
from typing import Dict, Set


class ExecutionMode(str, Enum):
    """Execution mode of the trading system."""
    SIMULATION = "simulation"
    PAPER = "paper"
    LIVE = "live"


class TradingDay(str, Enum):
    """Trading days of the week."""
    MONDAY = "Monday"
    TUESDAY = "Tuesday"
    WEDNESDAY = "Wednesday"
    THURSDAY = "Thursday"
    FRIDAY = "Friday"
    SATURDAY = "Saturday"
    SUNDAY = "Sunday"


class SessionPhase(str, Enum):
    """Trading session lifecycle phases according to Indian Standard Time."""
    PRE_MARKET = "PRE_MARKET"            # 09:00:00 - 09:15:00
    MARKET_OPEN = "MARKET_OPEN"          # 09:15:00 - 09:16:00 (first opening minute)
    ORB_CALCULATION = "ORB_CALCULATION"  # 09:16:00 - 09:45:00 (opening range formation)
    ACTIVE_TRADING = "ACTIVE_TRADING"    # 09:45:00 - 15:10:00 (breakouts & reversals)
    SQUARE_OFF = "SQUARE_OFF"            # 15:10:00 - 15:15:00 (mandatory exit)
    POST_MARKET = "POST_MARKET"          # 15:15:00 - 15:30:00
    CLOSED = "CLOSED"                    # 15:30:00+ and non-trading days


class StrategyState(str, Enum):
    """State machine states for the NIFTY ORB strategy."""
    IDLE = "IDLE"
    CALCULATING_RANGE = "CALCULATING_RANGE"
    RANGE_ESTABLISHED = "RANGE_ESTABLISHED"
    LONG_CE = "LONG_CE"
    LONG_PE = "LONG_PE"
    REVERSING_CE_TO_PE = "REVERSING_CE_TO_PE"
    REVERSING_PE_TO_CE = "REVERSING_PE_TO_CE"
    PROFIT_LOCKED = "PROFIT_LOCKED"
    LOCKED_OUT_FOR_DAY = "LOCKED_OUT_FOR_DAY"
    SQUARED_OFF = "SQUARED_OFF"


class SignalType(str, Enum):
    """Strategy signal types."""
    ENTER_LONG_CE = "ENTER_LONG_CE"
    ENTER_LONG_PE = "ENTER_LONG_PE"
    CLOSE_CE = "CLOSE_CE"
    CLOSE_PE = "CLOSE_PE"
    REVERSE_TO_PE = "REVERSE_TO_PE"
    REVERSE_TO_CE = "REVERSE_TO_CE"
    FORCE_SQUARE_OFF = "FORCE_SQUARE_OFF"


class OrderSide(IntEnum):
    """Order side compliant with FYERS API v3 (1: Buy, -1: Sell)."""
    BUY = 1
    SELL = -1


class OrderType(IntEnum):
    """Order types compliant with FYERS API v3."""
    LIMIT = 1
    MARKET = 2
    STOP_LOSS_MARKET = 3
    STOP_LOSS_LIMIT = 4


class OrderStatus(IntEnum):
    """Order status codes strictly aligned with FYERS API v3 status codes."""
    NEW = 0          # Local internal state before API response
    CANCELLED = 1    # Order cancelled
    FILLED = 2       # Traded / Filled
    TRANSIT = 4      # In transit to exchange
    REJECTED = 5     # Order rejected by broker/exchange
    PENDING = 6      # Open / Pending in orderbook
    EXPIRED = 7      # Expired at session end


class ProductType(str, Enum):
    """Product types for order execution."""
    INTRADAY = "INTRADAY"
    MARGIN = "MARGIN"
    CNC = "CNC"


class OptionType(str, Enum):
    """Option contract types."""
    CE = "CE"
    PE = "PE"


class RiskTriggerReason(str, Enum):
    """Reasons for risk management actions and kill-switch activations."""
    DAILY_LOSS_LIMIT_BREACHED = "DAILY_LOSS_LIMIT_BREACHED"
    PROFIT_PROTECTION_EXIT = "PROFIT_PROTECTION_EXIT"
    MANDATORY_EOD_SQUARE_OFF = "MANDATORY_EOD_SQUARE_OFF"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"
    RECONCILIATION_DESYNC = "RECONCILIATION_DESYNC"
    MAX_ORDER_RETRIES_EXCEEDED = "MAX_ORDER_RETRIES_EXCEEDED"


class AuditEventType(str, Enum):
    """Audit trail classification for compliance and post-trade analysis."""
    SESSION_BOOTSTRAP = "SESSION_BOOTSTRAP"
    CONFIG_LOADED = "CONFIG_LOADED"
    RANGE_ESTABLISHED = "RANGE_ESTABLISHED"
    SIGNAL_EMITTED = "SIGNAL_EMITTED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_STATE_TRANSITION = "ORDER_STATE_TRANSITION"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_CLOSED = "POSITION_CLOSED"
    REVERSAL_INITIATED = "REVERSAL_INITIATED"
    RISK_LIMIT_BREACH = "RISK_LIMIT_BREACH"
    KILL_SWITCH_ACTIVATED = "KILL_SWITCH_ACTIVATED"
    RECONCILIATION_ALERT = "RECONCILIATION_ALERT"
    SESSION_FINALIZED = "SESSION_FINALIZED"


# ------------------------------------------------------------------------------
# Explicitly Unresolved Strategy Semantics Enums
# ------------------------------------------------------------------------------

class BreakoutConfirmationMode(str, Enum):
    """Unresolved: Whether breakout triggers on candle close or intrabar price breach."""
    CANDLE_CLOSE = "candle_close"
    INTRABAR_TICK = "intrabar_tick"


class RangeBoundaryMode(str, Enum):
    """Unresolved: Exact bar boundary inclusion for the 09:16 to 09:45 window."""
    UP_TO_0944_CLOSE = "up_to_0944_close"  # 09:16:00 through 09:44:59 (ends at 09:45:00)
    UP_TO_0945_CLOSE = "up_to_0945_close"  # 09:16:00 through 09:45:59 (ends at 09:46:00)


class OrderExecutionStyle(str, Enum):
    """Unresolved: Option order entry execution style."""
    MARKET = "market"
    MARKETABLE_LIMIT = "marketable_limit"


class ExpiryDaySelectionMode(str, Enum):
    """Unresolved: Option contract selection on expiry day."""
    CURRENT_WEEKLY_0DTE = "current_weekly_0dte"
    NEXT_WEEKLY = "next_weekly"


class ProfitTrailingMode(str, Enum):
    """Unresolved: Specific profit-protection trailing mechanics."""
    LOCK_MINIMUM_ONLY = "lock_minimum_only"
    LOCK_AND_TRAIL = "lock_and_trail"


# ------------------------------------------------------------------------------
# Valid State Transition Graphs
# ------------------------------------------------------------------------------

VALID_ORDER_TRANSITIONS: Dict[OrderStatus, Set[OrderStatus]] = {
    OrderStatus.NEW: {OrderStatus.TRANSIT, OrderStatus.PENDING, OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED},
    OrderStatus.TRANSIT: {OrderStatus.PENDING, OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED},
    OrderStatus.PENDING: {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REJECTED},
    # Terminal states have no valid subsequent transitions
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.EXPIRED: set(),
}

VALID_STRATEGY_TRANSITIONS: Dict[StrategyState, Set[StrategyState]] = {
    StrategyState.IDLE: {StrategyState.CALCULATING_RANGE, StrategyState.LOCKED_OUT_FOR_DAY, StrategyState.SQUARED_OFF},
    StrategyState.CALCULATING_RANGE: {StrategyState.RANGE_ESTABLISHED, StrategyState.LOCKED_OUT_FOR_DAY, StrategyState.SQUARED_OFF},
    StrategyState.RANGE_ESTABLISHED: {StrategyState.LONG_CE, StrategyState.LONG_PE, StrategyState.LOCKED_OUT_FOR_DAY, StrategyState.SQUARED_OFF},
    StrategyState.LONG_CE: {StrategyState.REVERSING_CE_TO_PE, StrategyState.RANGE_ESTABLISHED, StrategyState.PROFIT_LOCKED, StrategyState.LOCKED_OUT_FOR_DAY, StrategyState.SQUARED_OFF},
    StrategyState.LONG_PE: {StrategyState.REVERSING_PE_TO_CE, StrategyState.RANGE_ESTABLISHED, StrategyState.PROFIT_LOCKED, StrategyState.LOCKED_OUT_FOR_DAY, StrategyState.SQUARED_OFF},
    StrategyState.REVERSING_CE_TO_PE: {StrategyState.LONG_PE, StrategyState.RANGE_ESTABLISHED, StrategyState.LOCKED_OUT_FOR_DAY, StrategyState.SQUARED_OFF},
    StrategyState.REVERSING_PE_TO_CE: {StrategyState.LONG_CE, StrategyState.RANGE_ESTABLISHED, StrategyState.LOCKED_OUT_FOR_DAY, StrategyState.SQUARED_OFF},
    StrategyState.PROFIT_LOCKED: {StrategyState.SQUARED_OFF, StrategyState.LOCKED_OUT_FOR_DAY},
    # Terminal session states
    StrategyState.LOCKED_OUT_FOR_DAY: {StrategyState.IDLE},  # Only upon next session reset
    StrategyState.SQUARED_OFF: {StrategyState.IDLE},         # Only upon next session reset
}


def can_transition_order(current: OrderStatus, target: OrderStatus) -> bool:
    """Check if an order status transition is valid."""
    if current == target:
        return True
    return target in VALID_ORDER_TRANSITIONS.get(current, set())


def can_transition_strategy(current: StrategyState, target: StrategyState) -> bool:
    """Check if a strategy state transition is valid."""
    if current == target:
        return True
    return target in VALID_STRATEGY_TRANSITIONS.get(current, set())


# ------------------------------------------------------------------------------
# Strategy Template Types & Workstation Configuration
# ------------------------------------------------------------------------------

class StrategyTemplateType(str, Enum):
    """Supported strategy templates in the trading workstation."""
    NIFTY_ORB = "NIFTY_ORB"
    INTRADAY_STRADDLE = "INTRADAY_STRADDLE"
    MOMENTUM_BREAKOUT = "MOMENTUM_BREAKOUT"


# ------------------------------------------------------------------------------
# Risk Pre-Trade Validation & Kill-Switch States
# ------------------------------------------------------------------------------

class RiskRejectReason(str, Enum):
    """Structured reason codes for pre-trade risk rejections."""
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    DAILY_LOSS_BREACHED = "DAILY_LOSS_BREACHED"
    PROFIT_PROTECTION_LOCKED = "PROFIT_PROTECTION_LOCKED"
    MANDATORY_EXIT_PASSED = "MANDATORY_EXIT_PASSED"
    OUTSIDE_TRADING_HOURS = "OUTSIDE_TRADING_HOURS"
    NON_TRADING_DAY = "NON_TRADING_DAY"
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    INVALID_CONTRACT = "INVALID_CONTRACT"
    INVALID_LOT_SIZE = "INVALID_LOT_SIZE"
    INVALID_ORDER_QTY = "INVALID_ORDER_QTY"
    UNSUPPORTED_ORDER_TYPE = "UNSUPPORTED_ORDER_TYPE"
    CONFLICTING_ORDER_STATE = "CONFLICTING_ORDER_STATE"
    DUPLICATE_ORDER = "DUPLICATE_ORDER"
    EXECUTION_MODE_DISABLED = "EXECUTION_MODE_DISABLED"


class KillSwitchState(str, Enum):
    """Explicit state machine states for the centralized kill switch coordinator."""
    NORMAL = "NORMAL"
    TRIGGERED = "TRIGGERED"
    EXIT_REQUESTED = "EXIT_REQUESTED"
    LOCKED_OUT = "LOCKED_OUT"

