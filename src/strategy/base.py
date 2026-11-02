"""Abstract Base Strategy Interface for MyTradeView.

Defines the event-driven lifecycle methods and contract for all trading strategies.
"""

from abc import ABC, abstractmethod
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from src.common.constants import RiskTriggerReason, StrategyState
from src.core.models import (
    Candle,
    Fill,
    MarketTick,
    Position,
    StrategyExecutionState,
    StrategySignal,
)


class BaseStrategy(ABC):
    """
    Abstract Base Class for event-driven algorithmic trading strategies.
    
    All lifecycle methods receive discrete market data or broker events
    and return zero or more validated StrategySignal instances.
    """

    @abstractmethod
    def on_session_start(self, session_id: str, session_date: date) -> None:
        """Initialize or reset strategy state for a new trading session."""
        pass

    @abstractmethod
    def on_candle(self, candle: Candle) -> List[StrategySignal]:
        """
        Process a completed candlestick bar.
        
        Args:
            candle: Validated Candle object in UTC.
            
        Returns:
            List of generated StrategySignal instances (empty if no action required).
        """
        pass

    @abstractmethod
    def on_tick(self, tick: MarketTick) -> List[StrategySignal]:
        """
        Process a live market tick.
        
        Args:
            tick: Validated MarketTick object in UTC.
            
        Returns:
            List of generated StrategySignal instances (empty if no action required).
        """
        pass

    @abstractmethod
    def on_order_fill(self, fill: Fill) -> List[StrategySignal]:
        """
        Process execution fill report from OMS / broker.
        Crucial for atomic multi-step workflows like reversal sequencing.
        
        Args:
            fill: Validated Fill object.
            
        Returns:
            List of subsequent StrategySignal instances (e.g. entry after exit fill).
        """
        pass

    @abstractmethod
    def on_position_update(self, position: Position) -> List[StrategySignal]:
        """Process updated position state and P&L."""
        pass

    @abstractmethod
    def on_mtm_update(
        self,
        realized_pnl: Decimal,
        unrealized_pnl: Decimal,
        timestamp: Optional[datetime] = None,
    ) -> List[StrategySignal]:
        """
        Process real-time MTM updates and evaluate daily loss limits and profit protection.
        
        Args:
            realized_pnl: Total realized PnL for the session in INR.
            unrealized_pnl: Total unrealized PnL of open positions in INR.
            timestamp: Evaluation timestamp.
            
        Returns:
            List of exit or emergency square-off signals if risk boundaries breached.
        """
        pass

    @abstractmethod
    def on_risk_event(
        self,
        reason: RiskTriggerReason,
        timestamp: Optional[datetime] = None,
    ) -> List[StrategySignal]:
        """Process external risk control triggers (e.g. kill-switch activation)."""
        pass

    @abstractmethod
    def on_session_end(self, timestamp: Optional[datetime] = None) -> List[StrategySignal]:
        """Finalize strategy session, ensure all positions are flat, and record audit summary."""
        pass

    @property
    @abstractmethod
    def state(self) -> StrategyState:
        """Current state in the strategy state machine."""
        pass

    @property
    @abstractmethod
    def execution_state(self) -> StrategyExecutionState:
        """Authoritative execution state snapshot."""
        pass
