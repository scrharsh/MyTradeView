"""Configurable Profit-Protection Engine with dynamic trailing and duplicate exit suppression.

Implements the verified StockMock profit lock (+₹3,000 trigger -> +₹500 floor)
and dynamic step-based trailing with strict idempotency.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
import logging
from typing import Optional

from src.common.constants import ProfitTrailingMode
from src.common.time_utils import now_utc

logger = logging.getLogger(__name__)


class ProfitProtectionStatus(str, Enum):
    """Lifecycle status of the profit-protection controller."""
    INACTIVE = "INACTIVE"
    ACTIVATED = "ACTIVATED"
    EXIT_REQUESTED = "EXIT_REQUESTED"
    EXIT_CONFIRMED = "EXIT_CONFIRMED"


class ProfitProtectionEngine:
    """
    Guarantees intraday gain retention by establishing a locked floor once
    the activation threshold is breached, and trailing upward on continued gains.
    """

    def __init__(
        self,
        trigger_profit: Decimal = Decimal("3000.00"),
        min_lock: Decimal = Decimal("500.00"),
        step_profit: Decimal = Decimal("1000.00"),
        step_lock: Decimal = Decimal("500.00"),
        mode: ProfitTrailingMode = ProfitTrailingMode.LOCK_MINIMUM_ONLY,
    ) -> None:
        if min_lock >= trigger_profit:
            raise ValueError(f"min_lock ({min_lock}) must be strictly less than trigger_profit ({trigger_profit})")

        self.trigger_profit = trigger_profit
        self.min_lock = min_lock
        self.step_profit = step_profit
        self.step_lock = step_lock
        self.mode = mode

        self._status: ProfitProtectionStatus = ProfitProtectionStatus.INACTIVE
        self._peak_mtm: Decimal = Decimal("0.00")
        self._current_floor: Decimal = Decimal("0.00")
        self._step_count: int = 0
        self._activated_at: Optional[datetime] = None
        self._exit_triggered_at: Optional[datetime] = None

    @property
    def status(self) -> ProfitProtectionStatus:
        """Current lifecycle status of profit protection."""
        return self._status

    @property
    def is_activated(self) -> bool:
        """Whether profit protection has crossed the activation threshold."""
        return self._status != ProfitProtectionStatus.INACTIVE

    @property
    def current_floor(self) -> Decimal:
        """Currently enforced minimum profit floor in INR."""
        return self._current_floor

    @property
    def peak_mtm(self) -> Decimal:
        """Peak strategy MTM observed while protection was monitored."""
        return self._peak_mtm

    @property
    def step_count(self) -> int:
        """Number of trailing step advancements completed."""
        return self._step_count

    def evaluate(self, current_mtm: Decimal, timestamp: Optional[datetime] = None) -> bool:
        """
        Evaluate current MTM against profit protection trigger and trailing floor.
        
        Returns:
            True if floor is breached and an exit must be dispatched (fires exactly once).
            False otherwise.
        """
        ts = timestamp or now_utc()

        # Update high-water mark
        if current_mtm > self._peak_mtm:
            self._peak_mtm = current_mtm

        # 1. Activation Phase
        if self._status == ProfitProtectionStatus.INACTIVE:
            if self._peak_mtm >= self.trigger_profit:
                self._status = ProfitProtectionStatus.ACTIVATED
                self._current_floor = self.min_lock
                self._activated_at = ts
                logger.info(
                    f"[ProfitProtection] Activated! Peak MTM={self._peak_mtm} >= Trigger={self.trigger_profit}. "
                    f"Baseline profit floor locked at ₹{self._current_floor}."
                )

        # 2. Dynamic Trailing Phase (if activated and lock_and_trail)
        if self._status == ProfitProtectionStatus.ACTIVATED and self.mode == ProfitTrailingMode.LOCK_AND_TRAIL:
            excess_profit = self._peak_mtm - self.trigger_profit
            if excess_profit >= self.step_profit:
                completed_steps = int(excess_profit // self.step_profit)
                if completed_steps > self._step_count:
                    self._step_count = completed_steps
                    self._current_floor = self.min_lock + (Decimal(completed_steps) * self.step_lock)
                    logger.info(
                        f"[ProfitProtection] Trailing floor advanced to ₹{self._current_floor} "
                        f"(Step {self._step_count}, Peak MTM=₹{self._peak_mtm})"
                    )

        # 3. Breach & Exit Request Phase (Strictly Idempotent)
        if self._status == ProfitProtectionStatus.ACTIVATED:
            if current_mtm <= self._current_floor:
                self._status = ProfitProtectionStatus.EXIT_REQUESTED
                self._exit_triggered_at = ts
                logger.warning(
                    f"[ProfitProtection] Floor breached! Current MTM=₹{current_mtm} <= Floor=₹{self._current_floor}. "
                    f"Dispatching exit request."
                )
                return True

        # Suppress further exit requests if already in EXIT_REQUESTED or EXIT_CONFIRMED
        return False

    def confirm_exit(self) -> None:
        """Record that the exit order has been confirmed filled by the OMS."""
        if self._status == ProfitProtectionStatus.EXIT_REQUESTED:
            self._status = ProfitProtectionStatus.EXIT_CONFIRMED
            logger.info("[ProfitProtection] Exit confirmed filled. Profit secured.")

    def reset_for_new_session(self) -> None:
        """Reset state for a new trading day."""
        self._status = ProfitProtectionStatus.INACTIVE
        self._peak_mtm = Decimal("0.00")
        self._current_floor = Decimal("0.00")
        self._step_count = 0
        self._activated_at = None
        self._exit_triggered_at = None

