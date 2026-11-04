"""FYERS API v3 Market Data Adapter for historical and live quotes."""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple
import pytz

from src.common.exceptions import (
    BrokerConnectionError,
    BrokerError,
    DataValidationError,
)
from src.common.time_utils import to_utc
from src.core.models import Candle, Instrument, MarketTick, OptionContract
from src.data.base import MarketDataProvider
from src.data.cache import MarketDataCache
from src.data.instrument_master import InstrumentMasterRegistry


class FyersMarketDataAdapter(MarketDataProvider):
    """
    Adapter implementing the MarketDataProvider interface using official FYERS API v3 endpoints.
    Normalizes FYERS responses into domain models and strictly separates market data from execution.
    """

    def __init__(
        self,
        client: Optional[Any] = None,
        cache: Optional[MarketDataCache] = None,
        credentials_provider: Optional[Callable[[], Optional[Tuple[str, str]]]] = None,
        socket_factory: Optional[Callable[..., Any]] = None,
        instrument_registry: Optional[InstrumentMasterRegistry] = None,
    ) -> None:
        """
        Initialize adapter with optional fyersModel client and response cache.
        If client is None, operates in offline/mock mode for testing.
        ``credentials_provider`` enables live tick streaming (returns (client_id, token)).
        """
        self._client = client
        self._cache = cache
        self._credentials_provider = credentials_provider
        self._socket_factory = socket_factory
        self._instrument_registry = instrument_registry
        self.last_stream_feed: Optional[Any] = None

    # --------------------------------------------------------------------------
    # 1. Historical Candles (/api/v3/history)
    # --------------------------------------------------------------------------

    async def get_historical_candles(
        self,
        symbol: str,
        resolution: str,
        start_time: datetime,
        end_time: datetime,
    ) -> List[Candle]:
        """
        Fetch historical candle data via FYERS /api/v3/history endpoint.
        Normalizes FYERS array format: [epoch, open, high, low, close, volume]
        into validated domain Candle models using Decimal prices.
        """
        start_epoch = int(to_utc(start_time).timestamp())
        end_epoch = int(to_utc(end_time).timestamp())

        cache_key = f"fyers_hist_{symbol}_{resolution}_{start_epoch}_{end_epoch}".replace(":", "_")
        if self._cache:
            cached_data = self._cache.get(cache_key)
            if cached_data is not None:
                return [Candle(**c) for c in cached_data]

        payload = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",  # 1: Epoch timestamp format
            "range_from": str(start_epoch),
            "range_to": str(end_epoch),
            "cont_flag": "1" if "INDEX" not in symbol else "0",
        }

        if self._client is None:
            raise BrokerConnectionError(
                "FYERS client is not configured or in offline test mode. "
                "Provide an authenticated FyersModel instance or use offline historical provider."
            )

        try:
            # FYERS SDK call (executed synchronously or wrapped in asyncio)
            response = self._client.history(data=payload)
        except Exception as e:
            raise BrokerConnectionError(f"FYERS network failure while querying history for {symbol}: {e}")

        candles = self.parse_history_response(response, symbol)

        if self._cache:
            self._cache.put(
                cache_key=cache_key,
                data=[c.model_dump(mode="json") for c in candles],
                source="FYERS_API_v3_history",
                contract_identifier=symbol,
                record_count=len(candles),
                coverage_start=start_time.isoformat(),
                coverage_end=end_time.isoformat(),
            )

        return candles

    def parse_history_response(self, response: Dict[str, Any], symbol: str) -> List[Candle]:
        """
        Pure parser: Normalizes FYERS history response payload.
        Handles rate limits, authentication failures, empty arrays, and candle validation.
        """
        if not response or not isinstance(response, dict):
            raise BrokerError(f"Empty or malformed history response from FYERS for {symbol}")

        status = response.get("s")
        if status == "error":
            code = response.get("code")
            msg = response.get("message", "Unknown error")
            if code in (-15, -16):
                raise BrokerConnectionError(f"FYERS Authentication Token Expired or Invalid (Code {code}): {msg}")
            if code == 429:
                raise BrokerConnectionError(f"FYERS API Rate Limit Exceeded (Code 429): {msg}")
            raise BrokerError(f"FYERS returned error for {symbol} (Code {code}): {msg}")

        raw_candles = response.get("candles", [])
        if not raw_candles:
            return []

        parsed: List[Candle] = []
        for row in raw_candles:
            if len(row) < 5:
                continue
            try:
                epoch_ts = int(row[0])
                ts = datetime.fromtimestamp(epoch_ts, tz=timezone.utc)
                candle = Candle(
                    symbol=symbol,
                    timestamp=ts,
                    open=Decimal(str(row[1])),
                    high=Decimal(str(row[2])),
                    low=Decimal(str(row[3])),
                    close=Decimal(str(row[4])),
                    volume=int(row[5]) if len(row) > 5 else 0,
                )
                parsed.append(candle)
            except Exception:
                continue

        return parsed

    # --------------------------------------------------------------------------
    # 2. Market Quotes (/api/v3/quotes)
    # --------------------------------------------------------------------------

    async def get_quote(self, symbol: str) -> MarketTick:
        """Fetch real-time market quote using FYERS /api/v3/quotes endpoint."""
        if self._client is None:
            raise BrokerConnectionError("FYERS client is not configured. Real-time quote unavailable.")

        try:
            response = self._client.quotes(data={"symbols": symbol})
        except Exception as e:
            raise BrokerConnectionError(f"FYERS network failure while fetching quote for {symbol}: {e}")

        return self.parse_quote_response(response, symbol)

    def parse_quote_response(self, response: Dict[str, Any], symbol: str) -> MarketTick:
        """Pure parser: Normalizes FYERS quote response payload into MarketTick."""
        if not response or response.get("s") != "ok":
            msg = response.get("message") if response else "Empty response"
            raise BrokerError(f"Failed to fetch FYERS quote for {symbol}: {msg}")

        data_list = response.get("d", [])
        if not data_list:
            raise DataValidationError(f"No quote data returned for {symbol}")

        item = data_list[0]
        v = item.get("v", {})
        lp = v.get("lp")
        if lp is None or lp <= 0:
            raise DataValidationError(f"Invalid LTP received from FYERS quote for {symbol}: {lp}")

        epoch = v.get("cmd", {}).get("t", int(datetime.now(timezone.utc).timestamp()))
        ts = datetime.fromtimestamp(epoch, tz=timezone.utc)

        bid = Decimal(str(v.get("bid"))) if v.get("bid") is not None else None
        ask = Decimal(str(v.get("ask"))) if v.get("ask") is not None else None

        return MarketTick(
            symbol=symbol,
            ltp=Decimal(str(lp)),
            volume=int(v.get("volume", 0)),
            timestamp=ts,
            bid_price=bid,
            ask_price=ask,
        )

    # --------------------------------------------------------------------------
    # 3. Instrument Metadata & Contracts
    # --------------------------------------------------------------------------

    async def get_instrument(self, symbol: str) -> Optional[Instrument]:
        """Return metadata from the loaded authoritative instrument master."""
        if self._instrument_registry is None:
            return None
        contract = self._instrument_registry.get_contract_by_symbol(symbol)
        if contract is not None:
            return Instrument(
                symbol=contract.symbol,
                instrument_type="OPTIDX",
                tick_size=contract.tick_size,
                lot_size=contract.lot_size,
            )
        return None

    async def get_option_chain(self, underlying: str, expiry_date: date) -> List[OptionContract]:
        """Return the verified option chain from the loaded instrument master."""
        if self._instrument_registry is None:
            return []
        return [
            contract
            for contract in (
                self._instrument_registry.get_contract_by_symbol(symbol)
                for symbol in self._instrument_registry.symbols
            )
            if contract is not None
            and contract.underlying == underlying
            and contract.expiry_date == expiry_date
        ]

    async def get_available_expiries(self, underlying: str) -> List[date]:
        """Return sorted expiries from the loaded instrument master."""
        if self._instrument_registry is None:
            return []
        return self._instrument_registry.get_expiries(underlying)

    async def stream_ticks(self, symbols: List[str]) -> AsyncIterator[MarketTick]:
        """Stream genuine exchange-timestamped ticks via the FYERS v3 data WebSocket.

        Requires ``credentials_provider`` (returns ``(client_id, access_token)``).
        Raises BrokerConnectionError when unauthenticated; never yields fabricated ticks.
        Exactly one symbol is supported per stream.
        """
        if self._credentials_provider is None or self._credentials_provider() is None:
            raise BrokerConnectionError(
                "FYERS credentials are not available. Authenticate first; no ticks are fabricated."
            )
        if len(symbols) != 1:
            raise DataValidationError("stream_ticks supports exactly one symbol per stream.")

        from src.data.fyers_live_feed import FyersLiveFeed

        feed = FyersLiveFeed(
            symbol=symbols[0],
            credentials_provider=self._credentials_provider,
            socket_factory=self._socket_factory,
        )
        self.last_stream_feed = feed
        await feed.start()
        try:
            async for tick in feed.ticks():
                yield tick
        finally:
            await feed.stop()

    def get_source_metadata(self) -> Dict[str, Any]:
        """Adapter metadata and capabilities summary."""
        return {
            "provider_name": "FyersMarketDataAdapter",
            "broker": "FYERS",
            "api_version": "v3",
            "client_configured": self._client is not None,
            "cache_enabled": self._cache is not None,
        }
