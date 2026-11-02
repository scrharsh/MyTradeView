"""Centralized Kill-Switch Coordinator with explicit state machine transitions.

Coordinates emergency order cancellations, position closures, duplicate suppression,
and unbypassable session lockouts independently of any specific strategy template.
"""

from datetime import datetime
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from src.common.constants import KillSwitchState, RiskTriggerReason
from src.common.time_utils import now_utc
from src.core.models import BrokerOrder, Position

logger = logging.getLogger(__name__)


class KillSwitchCoordinator:
    """
    Central emergency controller managing life-safety actions across the workstation.
    
    Distinguishes clearly between:
    1. Cancelling in-flight orders.
    2. Submitting aggressive position closure requests.
    3. Receiving broker execution confirmation that positions are flat.
    4. Locking out the session for the remainder of the trading day.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id

        self._state: KillSwitchState = KillSwitchState.NORMAL
        self._reason: Optional[RiskTriggerReason] = None
        self._triggered_at: Optional[datetime] = None
        self._locked_out_at: Optional[datetime] = None
        self._details: Dict[str, Any] = {}

        # Tracking collections
        self._pending_cancellations: Set[str] = set()
        self._confirmed_cancellations: Set[str] = set()
        self._pending_closures: Set[str] = set()
        self._confirmed_closures: Set[str] = set()
        self._audit_log: List[Dict[str, Any]] = []

    @property
    def state(self) -> KillSwitchState:
        """Current state in the kill-switch state machine."""
        return self._state

    @property
    def is_active(self) -> bool:
        """Whether emergency controls are active (True in any non-NORMAL state)."""
        return self._state != KillSwitchState.NORMAL

    @property
    def can_trade(self) -> bool:
        """Whether normal order placement is permitted (True only in NORMAL)."""
        return self._state == KillSwitchState.NORMAL

    @property
    def reason(self) -> Optional[RiskTriggerReason]:
        """Trigger reason for activation."""
        return self._reason

    @property
    def triggered_at(self) -> Optional[datetime]:
        """Activation timestamp."""
        return self._triggered_at

    @property
    def locked_out_at(self) -> Optional[datetime]:
        """Lockout timestamp."""
        return self._locked_out_at

    @property
    def audit_log(self) -> List[Dict[str, Any]]:
        """Immutable audit trail of kill-switch actions."""
        return list(self._audit_log)

    def trigger_kill_switch(
        self,
        reason: Any = RiskTriggerReason.MANUAL_OVERRIDE,
        operator: str = "Admin",
        details: Optional[Dict[str, Any]] = None,
        timestamp: Optional[datetime] = None,
    ) -> bool:
        """Convenience method / alias for trigger()."""
        reason_enum = reason if isinstance(reason, RiskTriggerReason) else RiskTriggerReason.MANUAL_OVERRIDE
        d = dict(details or {})
        d["operator"] = operator
        if isinstance(reason, str):
            d["raw_reason"] = reason
        return self.trigger(reason=reason_enum, details=d, timestamp=timestamp)

    def trigger(
        self,
        reason: RiskTriggerReason,
        details: Optional[Dict[str, Any]] = None,
        timestamp: Optional[datetime] = None,
    ) -> bool:
        """
        Engage the emergency kill switch.
        
        Returns:
            True if kill-switch was newly engaged, False if already active.
        """
        if self._state != KillSwitchState.NORMAL:
            logger.warning(f"[KillSwitch] Activation ignored: Already active in state {self._state.value}")
            return False

        ts = timestamp or now_utc()
        self._state = KillSwitchState.TRIGGERED
        self._reason = reason
        self._triggered_at = ts
        self._details = details or {}

        self._record_audit_event("KILL_SWITCH_TRIGGERED", {
            "reason": reason.value,
            "details": self._details,
        }, ts)

        logger.critical(f"[KillSwitch] ENGAGED! Reason: {reason.value}, Session: {self.session_id}")
        return True

    def coordinate_emergency_actions(
        self,
        open_positions: List[Position],
        pending_orders: List[BrokerOrder],
    ) -> Tuple[List[str], List[str]]:
        """
        Calculate and track all required cancellations and position exit requests.
        Guarantees zero duplicate requests.
        
        Returns:
            Tuple of (order_ids_to_cancel, position_symbols_to_flatten).
        """
        if self._state not in {KillSwitchState.TRIGGERED, KillSwitchState.EXIT_REQUESTED}:
            return ([], [])

        order_ids_to_cancel: List[str] = []
        symbols_to_flatten: List[str] = []

        # 1. Collect pending orders needing cancellation
        for order in pending_orders:
            if order.client_order_id not in self._pending_cancellations and order.client_order_id not in self._confirmed_cancellations:
                self._pending_cancellations.add(order.client_order_id)
                order_ids_to_cancel.append(order.client_order_id)

        # 2. Collect open positions needing flattening
        for pos in open_positions:
            if pos.is_open and pos.symbol not in self._pending_closures and pos.symbol not in self._confirmed_closures:
                self._pending_closures.add(pos.symbol)
                symbols_to_flatten.append(pos.symbol)

        self._state = KillSwitchState.EXIT_REQUESTED

        self._record_audit_event("ACTIONS_DISPATCHED", {
            "cancellations": order_ids_to_cancel,
            "closures": symbols_to_flatten,
        })

        # If there are no working orders or open positions, proceed directly to LOCKED_OUT
        if not self._pending_cancellations and not self._pending_closures:
            self._transition_to_locked_out()

        return (order_ids_to_cancel, symbols_to_flatten)

    def confirm_cancellation(self, client_order_id: str) -> None:
        """Confirm that an in-flight order was successfully cancelled."""
        self._pending_cancellations.discard(client_order_id)
        self._confirmed_cancellations.add(client_order_id)
        self._record_audit_event("CANCELLATION_CONFIRMED", {"client_order_id": client_order_id})
        self._check_all_actions_resolved()

    def confirm_position_closed(self, symbol: str) -> None:
        """Confirm that an open position was successfully flattened."""
        self._pending_closures.discard(symbol)
        self._confirmed_closures.add(symbol)
        self._record_audit_event("CLOSURE_CONFIRMED", {"symbol": symbol})
        self._check_all_actions_resolved()

    def record_action_failure(self, identifier: str, action_type: str, reason: str) -> None:
        """
        Record failure of an emergency action (e.g. broker rejection of exit order).
        Crucially does NOT remove item from pending, preventing false lockout finalization.
        """
        logger.critical(
            f"[KillSwitch] Action '{action_type}' FAILED for '{identifier}': {reason}. "
            "Positions/orders remain un-flattened! Operator manual intervention or emergency retry required."
        )
        self._record_audit_event("ACTION_FAILED", {
            "identifier": identifier,
            "action_type": action_type,
            "failure_reason": reason,
        })

    def _check_all_actions_resolved(self) -> None:
        """Verify whether all pending actions have been resolved and lock out session."""
        if self._state == KillSwitchState.EXIT_REQUESTED:
            if not self._pending_cancellations and not self._pending_closures:
                self._transition_to_locked_out()

    def _transition_to_locked_out(self) -> None:
        """Finalize emergency lockout."""
        self._state = KillSwitchState.LOCKED_OUT
        self._locked_out_at = now_utc()
        self._record_audit_event("SESSION_LOCKED_OUT", {
            "reason": self._reason.value if self._reason else "UNKNOWN",
        })
        logger.critical(f"[KillSwitch] Session {self.session_id} permanently LOCKED OUT for remainder of day.")

    def _record_audit_event(self, action: str, details: Dict[str, Any], timestamp: Optional[datetime] = None) -> None:
        """Record an auditable entry."""
        self._audit_log.append({
            "timestamp": (timestamp or now_utc()).isoformat(),
            "state": self._state.value,
            "action": action,
            "details": details,
        })

    def reset_for_new_session(self, new_session_id: str) -> None:
        """Reset coordinator for the next eligible scheduled market session."""
        self.session_id = new_session_id
        self._state = KillSwitchState.NORMAL
        self._reason = None
        self._triggered_at = None
        self._locked_out_at = None
        self._details.clear()
        self._pending_cancellations.clear()
        self._confirmed_cancellations.clear()
        self._pending_closures.clear()
        self._confirmed_closures.clear()
        self._audit_log.clear()
