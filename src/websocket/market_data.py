"""Market data WebSocket service with symbol subscription, timestamp normalization, and fail-closed staleness detection."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Any, Callable, Dict, List, Optional, Set

from src.common.time_utils import IST_TZ, UTC_TZ, now_utc, to_ist, to_utc
from src.core.models import MarketTick
from src.websocket.base import BaseWebSocketService, WebSocketConnectionState

logger = logging.getLogger(__name__)


class MarketDataWebSocketService(BaseWebSocketService):
    """
    Manages live market-data WebSocket feeds, symbol subscriptions,
    tick normalization to UTC/IST, and fail-closed stale-data health monitoring.
    """

    def __init__(
        self,
        access_token: Optional[str] = None,
        stale_threshold_seconds: float = 5.0,
        initial_retry_delay: float = 1.0,
        max_retry_delay: float = 30.0,
        backoff_multiplier: float = 2.0,
    ) -> None:
        super().__init__(
            name="MarketDataWebSocket",
            initial_retry_delay=initial_retry_delay,
            max_retry_delay=max_retry_delay,
            backoff_multiplier=backoff_multiplier,
        )
        self.access_token = access_token
        self.stale_threshold_seconds = stale_threshold_seconds
        self._subscribed_symbols: Set[str] = set()
        self._last_tick_times: Dict[str, datetime] = {}
        self._last_tick: Optional[MarketTick] = None
        self._tick_handlers: List[Callable[[MarketTick], None]] = []

    @property
    def subscribed_symbols(self) -> Set[str]:
        return set(self._subscribed_symbols)

    @property
    def last_tick(self) -> Optional[MarketTick]:
        return self._last_tick

    def register_tick_handler(self, handler: Callable[[MarketTick], None]) -> None:
        """Register a callback for incoming normalized MarketTick events."""
        self._tick_handlers.append(handler)

    def subscribe(self, symbols: List[str]) -> None:
        """Add symbols to the subscription set and subscribe on socket if connected."""
        new_symbols = set(symbols) - self._subscribed_symbols
        self._subscribed_symbols.update(symbols)
        if new_symbols and self.is_connected:
            self._do_subscribe(list(new_symbols))
            self._set_state(WebSocketConnectionState.SUBSCRIBED, reason=f"Subscribed to {len(self._subscribed_symbols)} symbols")

    def unsubscribe(self, symbols: List[str]) -> None:
        """Remove symbols from subscription set."""
        for sym in symbols:
            self._subscribed_symbols.discard(sym)
            self._last_tick_times.pop(sym, None)
        if not self._subscribed_symbols and self.state == WebSocketConnectionState.SUBSCRIBED:
            self._set_state(WebSocketConnectionState.CONNECTED, reason="No active symbol subscriptions")

    def _do_subscribe(self, symbols: List[str]) -> None:
        """Hook to send subscription message to underlying socket."""
        logger.info(f"[{self.name}] Subscribing to symbols: {symbols}")

    async def _after_connected(self) -> None:
        """Re-subscribe to all registered symbols following connection or reconnection."""
        if self._subscribed_symbols:
            self._do_subscribe(list(self._subscribed_symbols))
            self._set_state(WebSocketConnectionState.SUBSCRIBED, reason="Restored symbol subscriptions")

    def normalize_tick_frame(self, raw_frame: Dict[str, Any]) -> MarketTick:
        """
        Normalize FYERS market data frame into timezone-aware MarketTick.
        Handles epoch seconds, epoch milliseconds, and iso strings.
        """
        symbol = raw_frame.get("symbol") or raw_frame.get("name")
        if not symbol:
            raise ValueError(f"Missing symbol in tick frame: {raw_frame}")

        ltp_val = raw_frame.get("ltp") or raw_frame.get("last_price") or raw_frame.get("lp")
        if ltp_val is None:
            raise ValueError(f"Missing last traded price (ltp) in frame: {raw_frame}")
        ltp = Decimal(str(ltp_val))

        vol_val = raw_frame.get("vol_traded_today") or raw_frame.get("volume") or raw_frame.get("vol") or 0
        volume = int(vol_val)

        # Parse timestamp
        raw_ts = raw_frame.get("timestamp") or raw_frame.get("ts") or raw_frame.get("time")
        if raw_ts is None:
            ts_utc = now_utc()
        elif isinstance(raw_ts, (int, float)):
            # Epoch milliseconds vs seconds check
            if raw_ts > 1e11:
                ts_utc = datetime.fromtimestamp(raw_ts / 1000.0, tz=timezone.utc)
            else:
                ts_utc = datetime.fromtimestamp(raw_ts, tz=timezone.utc)
        elif isinstance(raw_ts, datetime):
            ts_utc = to_utc(raw_ts)
        elif isinstance(raw_ts, str):
            try:
                dt = datetime.fromisoformat(raw_ts)
                ts_utc = to_utc(dt)
            except Exception:
                ts_utc = now_utc()
        else:
            ts_utc = now_utc()

        bid = raw_frame.get("bid_price") or raw_frame.get("bid")
        ask = raw_frame.get("ask_price") or raw_frame.get("ask")
        bid_dec = Decimal(str(bid)) if bid is not None else None
        ask_dec = Decimal(str(ask)) if ask is not None else None

        return MarketTick(
            symbol=symbol,
            ltp=ltp,
            volume=volume,
            timestamp=ts_utc,
            bid_price=bid_dec,
            ask_price=ask_dec,
        )

    def process_incoming_frame(self, raw_frame: Dict[str, Any]) -> Optional[MarketTick]:
        """
        Parse, normalize, and distribute an incoming market data frame.
        Updates staleness tracking and notifies registered tick handlers.
        """
        try:
            tick = self.normalize_tick_frame(raw_frame)
            self.record_message_received()
            self._last_tick = tick
            self._last_tick_times[tick.symbol] = tick.timestamp

            for handler in self._tick_handlers:
                try:
                    handler(tick)
                except Exception as e:
                    logger.error(f"[{self.name}] Error in tick handler: {e}", exc_info=True)

            return tick
        except Exception as e:
            logger.warning(f"[{self.name}] Failed to process frame {raw_frame}: {e}")
            return None

    def is_data_healthy(self, symbol: Optional[str] = None) -> bool:
        """
        Fail-closed staleness health check.
        Returns False if:
        1. Not connected/subscribed.
        2. No tick received yet.
        3. Elapsed time since last tick exceeds stale_threshold_seconds.
        """
        if not self.is_connected:
            return False

        now = now_utc()
        if symbol:
            last_time = self._last_tick_times.get(symbol)
            if last_time is None:
                return False
            age = (now - last_time).total_seconds()
            if age > self.stale_threshold_seconds:
                self._set_state(WebSocketConnectionState.STALE, reason=f"Symbol {symbol} tick age {age:.1f}s exceeded threshold {self.stale_threshold_seconds}s")
                return False
            return True

        # Check overall service freshness across all subscriptions or last message
        if not self._last_message_at:
            return False
        age = (now - self._last_message_at).total_seconds()
        if age > self.stale_threshold_seconds:
            self._set_state(WebSocketConnectionState.STALE, reason=f"WebSocket data age {age:.1f}s exceeded threshold {self.stale_threshold_seconds}s")
            return False

        return True
