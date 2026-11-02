"""Deterministic client order token generation and duplicate-submission idempotency protection."""

from datetime import datetime
import hashlib
from typing import Dict, Optional, Set

from src.common.constants import OrderSide


class ClientOrderIdGenerator:
    """
    Produces deterministic, collision-free client order identifiers and FYERS tags.
    FYERS imposes a hard limit of 20 alphanumeric characters on order tags.
    """

    @staticmethod
    def generate(
        session_id: str,
        symbol: str,
        sequence_number: int,
        side: OrderSide,
    ) -> str:
        """
        Generate deterministic internal client_order_id:
        e.g. ORD_SESS20261106_0001_BUY
        """
        clean_sess = session_id.replace("SESSION_", "").replace("BT_SESS_", "").replace("-", "")
        return f"ORD_{clean_sess}_{sequence_number:04d}_{side.name}"

    @staticmethod
    def generate_fyers_tag(sequence_number: int, side: OrderSide) -> str:
        """
        Generate compliant FYERS order tag (max 20 characters).
        e.g. 'BUY_0001_ORB'
        """
        return f"{side.name}_{sequence_number:04d}_ORB"[:20]


class IdempotencyTracker:
    """
    Prevents duplicate submissions, double entries, and race conditions
    by tracking all active and completed order intents.
    """

    def __init__(self) -> None:
        self._registered_intents: Set[str] = set()
        self._registered_client_order_ids: Set[str] = set()
        self._intent_to_client_order: Dict[str, str] = {}

    def is_duplicate(self, intent_id: str, client_order_id: Optional[str] = None) -> bool:
        """Check if an intent or client_order_id has already been processed or submitted."""
        if intent_id in self._registered_intents:
            return True
        if client_order_id and client_order_id in self._registered_client_order_ids:
            return True
        return False

    def register(self, intent_id: str, client_order_id: str) -> None:
        """Register newly created intent and order identifier."""
        self._registered_intents.add(intent_id)
        self._registered_client_order_ids.add(client_order_id)
        self._intent_to_client_order[intent_id] = client_order_id

    def get_client_order_id(self, intent_id: str) -> Optional[str]:
        """Lookup client_order_id for an existing intent."""
        return self._intent_to_client_order.get(intent_id)

    def reset_for_session(self) -> None:
        """Clear memory cache for session reset."""
        self._registered_intents.clear()
        self._registered_client_order_ids.clear()
        self._intent_to_client_order.clear()
