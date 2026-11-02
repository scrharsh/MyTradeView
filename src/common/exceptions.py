"""Domain-specific typed exceptions for the trading system."""


class TradeViewError(Exception):
    """Base exception for all MyTradeView domain errors."""
    pass


class ConfigurationError(TradeViewError):
    """Raised when configuration values are missing, invalid, or unsafe."""
    pass


class UnconfirmedStrategySettingError(ConfigurationError):
    """Raised when execution is attempted while critical strategy decisions remain unconfirmed."""
    def __init__(self, setting_name: str, description: str):
        self.setting_name = setting_name
        self.description = description
        super().__init__(
            f"FAIL-CLOSED SAFETY INTERLOCK: Strategy setting '{setting_name}' is unconfirmed. "
            f"Details: {description}. Execution cannot proceed without explicit configuration."
        )


class InvalidStateTransitionError(TradeViewError):
    """Raised when an illegal state transition is attempted."""
    def __init__(self, entity: str, current_state: str, attempted_state: str):
        self.entity = entity
        self.current_state = current_state
        self.attempted_state = attempted_state
        super().__init__(
            f"Illegal {entity} transition: Cannot transition from {current_state} to {attempted_state}."
        )


class RiskLimitBreachedError(TradeViewError):
    """Raised when a pre-trade or in-trade risk limit has been violated."""
    def __init__(self, rule: str, current_value: str, threshold_value: str):
        self.rule = rule
        self.current_value = current_value
        self.threshold_value = threshold_value
        super().__init__(
            f"Risk rule '{rule}' violated: Current={current_value}, Threshold={threshold_value}."
        )


class KillSwitchActiveError(TradeViewError):
    """Raised when an operation is attempted while the emergency kill switch is engaged."""
    pass


class OrderValidationError(TradeViewError):
    """Raised when an order request fails validation constraints."""
    pass


class BrokerError(TradeViewError):
    """Base error for broker interaction failures."""
    pass


class BrokerConnectionError(BrokerError):
    """Raised when communication with broker endpoints or websockets fails."""
    pass


class BrokerOrderRejectedError(BrokerError):
    """Raised when an order is explicitly rejected by the broker or exchange RMS."""
    def __init__(self, order_id: str, code: int, message: str):
        self.order_id = order_id
        self.code = code
        self.message = message
        super().__init__(f"Broker rejected order {order_id} (Code {code}): {message}")


class ReconciliationError(TradeViewError):
    """Raised when local OMS state diverges critically from broker records."""
    pass


class DataValidationError(TradeViewError):
    """Raised when market data ticks or candles fail sanity/integrity checks."""
    pass
