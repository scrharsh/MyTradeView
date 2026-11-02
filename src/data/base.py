"""Abstract, provider-independent Market Data Provider interface."""

from abc import ABC, abstractmethod
from datetime import date, datetime
from decimal import Decimal
from typing import Any, AsyncIterator, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from src.core.models import Candle, Instrument, MarketTick, OptionContract


class DataQualityReport(BaseModel):
    """Diagnostic report capturing data integrity, anomalies, and coverage."""
    model_config = ConfigDict(frozen=True)

    symbol: str
    source: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    total_records: int = 0
    valid_records: int = 0
    duplicate_records: int = 0
    gap_intervals: int = 0
    anomalous_records: int = 0
    known_gaps: List[Dict[str, str]] = Field(default_factory=list)
    is_usable_for_backtest: bool = True
    diagnostic_messages: List[str] = Field(default_factory=list)


class MarketDataProvider(ABC):
    """
    Provider-independent Market Data contract.
    Decouples strategy engine and backtesting components from underlying sources
    (offline CSV/Parquet, live WebSocket, simulated feeds, FYERS broker).
    """

    @abstractmethod
    async def get_historical_candles(
        self,
        symbol: str,
        resolution: str,
        start_time: datetime,
        end_time: datetime,
    ) -> List[Candle]:
        """Fetch historical OHLCV candlestick bars for an underlying index or option contract."""
        pass

    @abstractmethod
    async def get_quote(self, symbol: str) -> MarketTick:
        """Fetch current market tick / quote for a symbol."""
        pass

    @abstractmethod
    async def get_instrument(self, symbol: str) -> Optional[Instrument]:
        """Fetch instrument metadata by trading symbol."""
        pass

    @abstractmethod
    async def get_option_chain(
        self,
        underlying: str,
        expiry_date: date,
    ) -> List[OptionContract]:
        """Fetch all available option strikes and contracts for given underlying and expiry."""
        pass

    @abstractmethod
    async def get_available_expiries(self, underlying: str) -> List[date]:
        """List all available option expiration dates sorted chronologically."""
        pass

    @abstractmethod
    async def stream_ticks(self, symbols: List[str]) -> AsyncIterator[MarketTick]:
        """Stream real-time or simulated market ticks."""
        pass

    @abstractmethod
    def get_source_metadata(self) -> Dict[str, Any]:
        """Return provider identification, connection status, and coverage metadata."""
        pass
