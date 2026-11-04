"""Standalone Mark-to-Market (MTM) Tracker using Decimal arithmetic.

Calculates realized P&L, unrealized P&L, total MTM, peak equity, and drawdown
with explicit staleness tracking and zero double-counting.
"""

from datetime import datetime
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional

from src.common.time_utils import now_utc
from src.core.models import Fill, Position
from src.risk.base import BaseMtmTracker, RiskSnapshot

logger = logging.getLogger(__name__)


class MtmTracker(BaseMtmTracker):
    """
    Precision Mark-to-Market accounting engine.
    
    Combines realized and unrealized P&L without double counting, evaluates
    portfolio exposure, and validates price freshness against a configurable staleness window.
    """

    def __init__(
        self,
        session_id: str,
        staleness_threshold_seconds: int = 60,
    ) -> None:
        self.session_id = session_id
        self.staleness_threshold_seconds = staleness_threshold_seconds

        self._positions: Dict[str, Position] = {}
        self._closed_positions: List[Position] = []
        self._price_timestamps: Dict[str, datetime] = {}
        self._peak_mtm: Decimal = Decimal("0.00")
        self._risk_events: List[Dict[str, Any]] = []
        self._is_data_stale: bool = False

    def reset(self) -> None:
        """Clear all tracked positions and reset MTM state."""
        self._positions.clear()
        self._closed_positions.clear()
        self._price_timestamps.clear()
        self._peak_mtm = Decimal("0.00")
        self._risk_events.clear()
        self._is_data_stale = False

    # --------------------------------------------------------------------------
    # Position & Fill Management
    # --------------------------------------------------------------------------

    def register_position(self, position: Position) -> None:
        """Register or update an active position in the tracker."""
        self._positions[position.symbol] = position
        self._recalculate_peak()

    def update_position(self, position: Position, current_ltp: Optional[Decimal] = None) -> RiskSnapshot:
        """
        Update position and recalculate snapshot.
        If current_ltp provided, updates position LTP and unrealized P&L.
        """
        self._positions[position.symbol] = position
        if current_ltp is not None:
            position.update_ltp(current_ltp)
        self._recalculate_peak()
        return self.get_snapshot()

    def on_fill(self, fill: Fill) -> None:
        """Apply execution fill report directly to tracked position."""
        if fill.symbol in self._positions:
            pos = self._positions[fill.symbol]
            # If previous trade on this symbol was completely closed, archive it to isolate new trade lifecycle
            if not pos.is_open and pos.closed_at is not None and pos.net_qty == 0:
                self._closed_positions.append(pos)
                pos = Position(session_id=self.session_id, symbol=fill.symbol)
                self._positions[fill.symbol] = pos
        else:
            pos = Position(session_id=self.session_id, symbol=fill.symbol)
            self._positions[fill.symbol] = pos

        pos.apply_fill(fill)
        self._price_timestamps[fill.symbol] = fill.timestamp
        self._recalculate_peak()

    # --------------------------------------------------------------------------
    # Market Data & Freshness Evaluation
    # --------------------------------------------------------------------------

    def update_market_price(
        self,
        symbol: str,
        ltp: Decimal,
        timestamp: Optional[datetime] = None,
    ) -> bool:
        """
        Update market price for a tracked symbol with staleness and sanity validation.
        
        Args:
            symbol: Contract or underlying symbol.
            ltp: Last traded price (must be positive Decimal).
            timestamp: Price arrival timestamp (UTC).
            
        Returns:
            True if price is fresh and valid, False if stale or invalid.
        """
        if ltp <= Decimal("0.00"):
            logger.warning(f"[Risk MTM] Rejected non-positive price for {symbol}: {ltp}")
            return False

        ts = timestamp or now_utc()
        age_seconds = (now_utc() - ts).total_seconds()

        is_stale = age_seconds > self.staleness_threshold_seconds
        self._is_data_stale = is_stale

        if is_stale:
            logger.warning(
                f"[Risk MTM] Stale price detected for {symbol}: age {age_seconds:.1f}s > threshold {self.staleness_threshold_seconds}s"
            )

        self._price_timestamps[symbol] = ts

        # Update position LTP if tracked
        if symbol in self._positions:
            self._positions[symbol].update_ltp(ltp)
            self._recalculate_peak()

        return not is_stale

    # --------------------------------------------------------------------------
    # Accounting & MTM Calculations
    # --------------------------------------------------------------------------

    @property
    def realized_pnl(self) -> Decimal:
        """Total realized P&L across all session positions without double counting."""
        total = Decimal("0.00")
        for pos in self._closed_positions:
            total += pos.realized_pnl
        for pos in self._positions.values():
            total += pos.realized_pnl
        return total.quantize(Decimal("0.01"))

    @property
    def unrealized_pnl(self) -> Decimal:
        """Total unrealized P&L across all currently open positions."""
        total = Decimal("0.00")
        for pos in self._positions.values():
            if pos.is_open:
                total += pos.unrealized_pnl
        return total.quantize(Decimal("0.01"))

    @property
    def total_mtm(self) -> Decimal:
        """Total strategy MTM: Realized P&L + Unrealized P&L."""
        return (self.realized_pnl + self.unrealized_pnl).quantize(Decimal("0.01"))

    @property
    def peak_mtm(self) -> Decimal:
        """High-water mark of cumulative strategy MTM for the session."""
        return self._peak_mtm

    @property
    def current_drawdown(self) -> Decimal:
        """Current drawdown from peak MTM in INR."""
        dd = self._peak_mtm - self.total_mtm
        return max(Decimal("0.00"), dd).quantize(Decimal("0.01"))

    @property
    def open_positions_count(self) -> int:
        """Number of active non-zero positions."""
        return sum(1 for pos in self._positions.values() if pos.is_open)

    @property
    def total_exposure_quantity(self) -> int:
        """Total absolute net quantity across all open contracts."""
        return sum(abs(pos.net_qty) for pos in self._positions.values() if pos.is_open)

    @property
    def is_data_stale(self) -> bool:
        """Whether any position has stale market data."""
        return self._is_data_stale

    def _recalculate_peak(self) -> None:
        """Update cumulative peak equity."""
        mtm = self.total_mtm
        if mtm > self._peak_mtm:
            self._peak_mtm = mtm

    def record_risk_event(self, event_type: str, details: Dict[str, Any]) -> None:
        """Append an auditable risk event to session history."""
        self._risk_events.append({
            "timestamp": now_utc().isoformat(),
            "event_type": event_type,
            "details": details,
            "mtm_snapshot": {
                "realized": str(self.realized_pnl),
                "unrealized": str(self.unrealized_pnl),
                "total_mtm": str(self.total_mtm),
            },
        })

    def get_risk_event_history(self) -> List[Dict[str, Any]]:
        """Retrieve copy of all recorded risk events."""
        return list(self._risk_events)

    def get_snapshot(
        self,
        is_kill_switch_active: bool = False,
        is_profit_protection_active: bool = False,
        profit_floor: Decimal = Decimal("0.00"),
    ) -> RiskSnapshot:
        """Produce an immutable snapshot of current MTM and risk metrics."""
        return RiskSnapshot(
            session_id=self.session_id,
            timestamp=now_utc(),
            realized_pnl=self.realized_pnl,
            unrealized_pnl=self.unrealized_pnl,
            total_mtm=self.total_mtm,
            peak_mtm=self.peak_mtm,
            current_drawdown=self.current_drawdown,
            is_kill_switch_active=is_kill_switch_active,
            is_profit_protection_active=is_profit_protection_active,
            profit_floor=profit_floor,
            open_positions_count=self.open_positions_count,
            is_data_stale=self._is_data_stale,
        )
