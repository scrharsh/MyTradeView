"""Abstract base interface for broker execution and query adapters."""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import List, Optional

from src.broker.models import (
    AuthStatus,
    BrokerConnectionStatus,
    BrokerFunds,
    BrokerOrderBookItem,
    BrokerOrderPayload,
    BrokerOrderResponse,
    BrokerPosition,
    BrokerTradeItem,
)
from src.core.models import MarketTick


class AbstractBrokerClient(ABC):
    """
    Decoupled interface shielding trading strategies, OMS, and risk components
    from broker-specific network protocols, response dictionaries, and error codes.
    """

    @property
    @abstractmethod
    def connection_status(self) -> BrokerConnectionStatus:
        """Current connectivity and authentication health status."""
        pass

    @abstractmethod
    async def check_auth(self) -> AuthStatus:
        """Inspect and validate broker authentication status."""
        pass

    @abstractmethod
    async def place_order(self, payload: BrokerOrderPayload) -> BrokerOrderResponse:
        """Submit a new order to the broker with idempotency protection."""
        pass

    @abstractmethod
    async def modify_order(
        self,
        broker_order_id: str,
        qty: Optional[int] = None,
        limit_price: Optional[Decimal] = None,
    ) -> BrokerOrderResponse:
        """Modify quantity or price of a pending working order."""
        pass

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResponse:
        """Cancel an open working order on the broker order book."""
        pass

    @abstractmethod
    async def get_order_book(self) -> List[BrokerOrderBookItem]:
        """Fetch all orders from the broker order book for the current session."""
        pass

    @abstractmethod
    async def get_trade_book(self) -> List[BrokerTradeItem]:
        """Fetch all confirmed execution trades from the broker trade book."""
        pass

    @abstractmethod
    async def get_positions(self) -> List[BrokerPosition]:
        """Fetch all current open and closed net positions from the broker."""
        pass

    @abstractmethod
    async def get_funds(self) -> BrokerFunds:
        """Fetch account balance, margin utilization, and available limits."""
        pass

    @abstractmethod
    async def get_quotes(self, symbols: List[str]) -> List[MarketTick]:
        """Fetch latest quotes for the specified symbols."""
        pass
