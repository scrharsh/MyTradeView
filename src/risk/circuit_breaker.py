"""Daily Strategy Loss Circuit Breaker and Session Lockout Coordinator.

Enforces hard loss thresholds (default -₹3,000) with atomic closure tracking,
duplicate request suppression, and full session lockout.
"""

from datetime import datetime
from decimal import Decimal
import logging
from typing import Dict, List, Optional, Set

from src.common.constants import RiskTriggerReason
from src.common.time_utils import now_utc
from src.core.models import Position

logger = logging.getLogger(__name__)


class DailyLossCircuitBreaker:
    """
    Guarantees hard portfolio containment by terminating trading when
    cumulative daily MTM drops below the configured loss threshold.
    """

    def __init__(self, max_daily_loss: Decimal = Decimal("3000.00")) -> None:
        self.max_daily_loss = abs(max_daily_loss)

        self._is_locked_out: bool = False
        self._triggered_at: Optional[datetime] = None
        self._trigger_mtm: Optional[Decimal] = None
        self._closure_requested: bool = False
        self._closure_confirmed: bool = False
        self._affected_symbols: Set[str] = set()
        self._pending_closures: Set[str] = set()

    @property
    def is_locked_out(self) -> bool:
        """Whether the daily loss limit has been breached and session is locked out."""
        return self._is_locked_out

    @property
    def can_trade(self) -> bool:
        """Whether trading entries are permitted (False once locked out)."""
        return not self._is_locked_out

    @property
    def triggered_at(self) -> Optional[datetime]:
        """Timestamp of initial circuit breaker breach."""
        return self._triggered_at

    @property
    def trigger_mtm(self) -> Optional[Decimal]:
        """MTM value at the moment the breach occurred."""
        return self._trigger_mtm

    @property
    def closure_requested(self) -> bool:
        """Whether position flattening has been dispatched to OMS."""
        return self._closure_requested

    @property
    def closure_confirmed(self) -> bool:
        """Whether all affected positions have been confirmed closed by OMS/broker."""
        return self._closure_confirmed

    def evaluate(
        self,
        current_mtm: Decimal,
        open_positions: Optional[List[Position]] = None,
        timestamp: Optional[datetime] = None,
    ) -> bool:
        """
        Evaluate current strategy MTM against max daily loss threshold.
        
        Returns:
            True if breach just occurred and emergency action is required.
            False if within limits or already locked out.
        """
        if self._is_locked_out:
            return False

        # Threshold condition: cumulative MTM <= -max_daily_loss
        if current_mtm <= -self.max_daily_loss:
            self._is_locked_out = True
            self._triggered_at = timestamp or now_utc()
            self._trigger_mtm = current_mtm

            if open_positions:
                for pos in open_positions:
                    if pos.is_open:
                        self._affected_symbols.add(pos.symbol)
                        self._pending_closures.add(pos.symbol)

            logger.critical(
                f"[CircuitBreaker] Hard daily loss limit breached! MTM={current_mtm} <= -{self.max_daily_loss}. "
                f"Locking session. Affected positions: {list(self._affected_symbols)}"
            )
            return True

        return False

    def request_closure(self) -> List[str]:
        """
        Request closure of all affected open positions.
        Suppresses duplicate requests if closure was already requested.
        
        Returns:
            List of symbols for which new closure requests should be submitted.
        """
        if self._closure_requested:
            return []

        self._closure_requested = True
        return list(self._pending_closures)

    def confirm_position_closed(self, symbol: str) -> bool:
        """
        Record verified closure of an affected position.
        
        Returns:
            True if all affected positions are now confirmed closed.
        """
        self._pending_closures.discard(symbol)
        if not self._pending_closures:
            self._closure_confirmed = True
            logger.info(f"[CircuitBreaker] All affected positions confirmed closed after daily loss breach.")
            return True
        return False

    def reset_for_new_session(self) -> None:
        """Reset circuit breaker state for a new trading session."""
        self._is_locked_out = False
        self._triggered_at = None
        self._trigger_mtm = None
        self._closure_requested = False
        self._closure_confirmed = False
        self._affected_symbols.clear()
        self._pending_closures.clear()
