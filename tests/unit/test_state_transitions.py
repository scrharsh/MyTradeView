"""Unit tests for state machine transitions and invalid transition rejection."""

from decimal import Decimal
import pytest

from src.common.constants import (
    OrderStatus,
    OrderType,
    OrderSide,
    ProductType,
    StrategyState,
)
from src.common.exceptions import InvalidStateTransitionError
from src.core.models import BrokerOrder, StrategyExecutionState


def test_order_valid_lifecycle():
    """Verify standard order lifecycle: NEW -> TRANSIT -> PENDING -> FILLED."""
    order = BrokerOrder(
        client_order_id="ORD_001",
        session_id="SESS_01",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        product_type=ProductType.INTRADAY,
        qty=25,
        remaining_qty=25,
        tag="TAG_001",
    )
    assert order.status == OrderStatus.NEW

    # NEW -> TRANSIT
    order.apply_transition(OrderStatus.TRANSIT)
    assert order.status == OrderStatus.TRANSIT

    # TRANSIT -> PENDING
    order.apply_transition(OrderStatus.PENDING)
    assert order.status == OrderStatus.PENDING

    # PENDING -> FILLED
    order.apply_transition(OrderStatus.FILLED, filled_qty=25, avg_price=Decimal("105.50"))
    assert order.status == OrderStatus.FILLED
    assert order.filled_qty == 25
    assert order.remaining_qty == 0
    assert order.avg_fill_price == Decimal("105.50")


def test_order_invalid_transition_rejected():
    """Verify that illegal transitions (e.g. FILLED -> PENDING) raise InvalidStateTransitionError."""
    order = BrokerOrder(
        client_order_id="ORD_002",
        session_id="SESS_01",
        symbol="NSE:NIFTY26N0624500CE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        product_type=ProductType.INTRADAY,
        qty=25,
        remaining_qty=25,
        tag="TAG_002",
    )
    order.apply_transition(OrderStatus.FILLED, filled_qty=25, avg_price=Decimal("100.00"))
    assert order.status == OrderStatus.FILLED

    # Attempting to move from terminal FILLED back to PENDING must raise error
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        order.apply_transition(OrderStatus.PENDING)
    assert "Cannot transition from FILLED to PENDING" in str(exc_info.value)


def test_strategy_valid_lifecycle():
    """Verify legal strategy transitions: IDLE -> CALCULATING_RANGE -> RANGE_ESTABLISHED -> LONG_CE -> REVERSING -> LONG_PE."""
    state = StrategyExecutionState(session_id="SESS_01")
    assert state.current_state == StrategyState.IDLE

    state.transition_to(StrategyState.CALCULATING_RANGE)
    assert state.current_state == StrategyState.CALCULATING_RANGE

    state.transition_to(StrategyState.RANGE_ESTABLISHED)
    assert state.current_state == StrategyState.RANGE_ESTABLISHED

    state.transition_to(StrategyState.LONG_CE)
    assert state.current_state == StrategyState.LONG_CE

    state.transition_to(StrategyState.REVERSING_CE_TO_PE)
    assert state.current_state == StrategyState.REVERSING_CE_TO_PE

    state.transition_to(StrategyState.LONG_PE)
    assert state.current_state == StrategyState.LONG_PE


def test_strategy_invalid_transition_rejected():
    """Verify that illegal strategy transitions (e.g. IDLE directly to LONG_CE) raise InvalidStateTransitionError."""
    state = StrategyExecutionState(session_id="SESS_01")
    assert state.current_state == StrategyState.IDLE

    # Skipping range formation directly into trade is forbidden
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        state.transition_to(StrategyState.LONG_CE)
    assert "Cannot transition from IDLE to LONG_CE" in str(exc_info.value)
