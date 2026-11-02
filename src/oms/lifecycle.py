"""Strict state machine and lifecycle transition validator for the Order Management System."""

from typing import Dict, Set

from src.common.exceptions import InvalidStateTransitionError
from src.oms.models import OmsOrderStatus

VALID_OMS_TRANSITIONS: Dict[OmsOrderStatus, Set[OmsOrderStatus]] = {
    OmsOrderStatus.INTENT_CREATED: {
        OmsOrderStatus.VALIDATING,
        OmsOrderStatus.REJECTED,
        OmsOrderStatus.FAILED,
    },
    OmsOrderStatus.VALIDATING: {
        OmsOrderStatus.SUBMISSION_PENDING,
        OmsOrderStatus.REJECTED,
        OmsOrderStatus.FAILED,
    },
    OmsOrderStatus.SUBMISSION_PENDING: {
        OmsOrderStatus.ACKNOWLEDGED,
        OmsOrderStatus.OPEN,
        OmsOrderStatus.PARTIALLY_FILLED,
        OmsOrderStatus.FILLED,
        OmsOrderStatus.REJECTED,
        OmsOrderStatus.FAILED,
        OmsOrderStatus.RECONCILIATION_REQUIRED,
    },
    OmsOrderStatus.ACKNOWLEDGED: {
        OmsOrderStatus.OPEN,
        OmsOrderStatus.PARTIALLY_FILLED,
        OmsOrderStatus.FILLED,
        OmsOrderStatus.CANCELLATION_REQUESTED,
        OmsOrderStatus.CANCELLED,
        OmsOrderStatus.REJECTED,
        OmsOrderStatus.FAILED,
        OmsOrderStatus.RECONCILIATION_REQUIRED,
    },
    OmsOrderStatus.OPEN: {
        OmsOrderStatus.PARTIALLY_FILLED,
        OmsOrderStatus.FILLED,
        OmsOrderStatus.CANCELLATION_REQUESTED,
        OmsOrderStatus.CANCELLED,
        OmsOrderStatus.REJECTED,
        OmsOrderStatus.FAILED,
        OmsOrderStatus.RECONCILIATION_REQUIRED,
    },
    OmsOrderStatus.PARTIALLY_FILLED: {
        OmsOrderStatus.PARTIALLY_FILLED,
        OmsOrderStatus.FILLED,
        OmsOrderStatus.CANCELLATION_REQUESTED,
        OmsOrderStatus.CANCELLED,
        OmsOrderStatus.RECONCILIATION_REQUIRED,
    },
    OmsOrderStatus.CANCELLATION_REQUESTED: {
        OmsOrderStatus.CANCELLED,
        OmsOrderStatus.FILLED,
        OmsOrderStatus.PARTIALLY_FILLED,
        OmsOrderStatus.RECONCILIATION_REQUIRED,
    },
    OmsOrderStatus.RECONCILIATION_REQUIRED: {
        OmsOrderStatus.OPEN,
        OmsOrderStatus.PARTIALLY_FILLED,
        OmsOrderStatus.FILLED,
        OmsOrderStatus.CANCELLED,
        OmsOrderStatus.REJECTED,
        OmsOrderStatus.FAILED,
    },
    # Terminal states: cannot transition to any further states
    OmsOrderStatus.FILLED: set(),
    OmsOrderStatus.CANCELLED: set(),
    OmsOrderStatus.REJECTED: set(),
    OmsOrderStatus.FAILED: set(),
}


def can_transition_oms_order(current: OmsOrderStatus, target: OmsOrderStatus) -> bool:
    """
    Evaluate if an OMS order state transition is legal according to the transition graph.
    Transitions to the identical state are idempotent (valid).
    """
    if current == target:
        return True
    return target in VALID_OMS_TRANSITIONS.get(current, set())


def validate_oms_transition(client_order_id: str, current: OmsOrderStatus, target: OmsOrderStatus) -> None:
    """
    Raise an InvalidStateTransitionError if the attempted transition is illegal.
    Prevents out-of-order broker messages from regressing terminal states.
    """
    if not can_transition_oms_order(current, target):
        raise InvalidStateTransitionError(
            entity=f"OmsOrder[{client_order_id}]",
            current_state=current.value,
            attempted_state=target.value,
        )
