"""FYERS API v3 market-data WebSocket feed (ticks only).

Wraps the official ``FyersDataSocket`` (runs in its own thread) and exposes an
asyncio-friendly event stream. Tracks four *separate* facts and never infers one
from another:

- ``ws_connected``  : socket opened and not since closed/errored
- ``subscribed``    : FYERS acknowledged the subscription
- ``tick_fresh``    : a valid exchange-timestamped tick was received within the stale window
- ``is_live``       : all of the above

No tick is ever fabricated: ticks without an exchange timestamp or valid price are
rejected and counted. Credentials never appear in logs, events or snapshots.
"""

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, AsyncIterator, Callable, Dict, Optional, Tuple

from src.common.exceptions import BrokerConnectionError
from src.common.time_utils import now_utc
from src.core.models import MarketTick

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedEvent:
    """kind: 'tick' | 'disconnect' | 'stale' | 'auth_failed'."""

    kind: str
    tick: Optional[MarketTick] = None
    reason: str = ""


SocketFactory = Callable[..., Any]


def default_socket_factory(
    access_token: str,
    on_message: Callable,
    on_error: Callable,
    on_connect: Callable,
    on_close: Callable,
) -> Any:
    """Build the official FYERS v3 data socket. ``access_token`` is ``client_id:token``."""
    from fyers_apiv3.FyersWebsocket import data_ws

    return data_ws.FyersDataSocket(
        access_token=access_token,
        log_path="",
        litemode=False,
        write_to_file=False,
        reconnect=True,
        on_connect=on_connect,
        on_close=on_close,
        on_error=on_error,
        on_message=on_message,
    )


class FyersLiveFeed:
    """Live tick feed for a single symbol with reconnection supervision."""

    def __init__(
        self,
        symbol: str,
        credentials_provider: Callable[[], Optional[Tuple[str, str]]],
        socket_factory: Optional[SocketFactory] = None,
        stale_after_seconds: float = 30.0,
        reconnect_after_seconds: float = 10.0,
        max_reconnect_attempts: int = 10,
        now_fn: Callable[[], datetime] = now_utc,
        max_future_skew_seconds: float = 120.0,
    ) -> None:
        self.symbol = symbol
        self._credentials_provider = credentials_provider
        self._socket_factory = socket_factory or default_socket_factory
        self.stale_after = timedelta(seconds=stale_after_seconds)
        self.reconnect_after = timedelta(seconds=reconnect_after_seconds)
        self.max_reconnect_attempts = max_reconnect_attempts
        self._now = now_fn
        self._max_future_skew = timedelta(seconds=max_future_skew_seconds)

        self._socket: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: "asyncio.Queue[FeedEvent]" = asyncio.Queue(maxsize=10000)
        self._supervisor: Optional[asyncio.Task] = None
        self._lock = threading.Lock()
        self._running = False

        self.ws_connected = False
        self.subscribed = False
        self.auth_failed = False
        self.last_error: str = ""
        self.last_connect_at: Optional[datetime] = None
        self.disconnected_at: Optional[datetime] = None
        self.last_tick_received_at: Optional[datetime] = None
        self.last_exchange_time: Optional[datetime] = None
        self.last_ltp: Optional[Decimal] = None
        self.reconnect_count = 0
        self._reconnect_attempts = 0
        self._stale_reported = False
        self.stats: Dict[str, int] = {
            "raw_ticks_received": 0,
            "exchange_timestamps_valid": 0,
            "ticks_received": 0,
            "ticks_rejected_no_timestamp": 0,
            "ticks_rejected_bad_price": 0,
            "ticks_rejected_future_timestamp": 0,
            "ticks_ignored_other_symbol": 0,
            "queue_overflow_dropped": 0,
        }

    # ------------------------------------------------------------------
    # Public state
    # ------------------------------------------------------------------
    @property
    def tick_fresh(self) -> bool:
        if self.last_tick_received_at is None:
            return False
        return (self._now() - self.last_tick_received_at) <= self.stale_after

    @property
    def is_live(self) -> bool:
        return self.ws_connected and self.subscribed and self.tick_fresh and not self.auth_failed

    @property
    def connection_state(self) -> str:
        if not self._running:
            return "STOPPED"
        if self.auth_failed:
            return "AUTH_FAILED"
        if not self.ws_connected:
            if self._reconnect_attempts >= self.max_reconnect_attempts:
                return "FAILED"
            return "CONNECTING" if self.last_connect_at is None else "RECONNECTING"
        if not self.subscribed:
            return "CONNECTED"
        if not self.tick_fresh:
            return "SUBSCRIBED_NO_FRESH_TICKS" if self.last_tick_received_at is None else "STALE"
        return "LIVE_STREAMING"

    def snapshot(self) -> Dict[str, Any]:
        """Sanitized, JSON-safe feed state."""
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        return {
            "symbol": self.symbol,
            "state": self.connection_state,
            "websocket_connected": self.ws_connected,
            "subscribed": self.subscribed,
            "fresh_tick_received": self.tick_fresh,
            "is_live": self.is_live,
            "last_tick_received_at": iso(self.last_tick_received_at),
            "last_exchange_time": iso(self.last_exchange_time),
            "reconnect_count": self.reconnect_count,
            "last_error": self.last_error,
            "stats": dict(self.stats),
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        if self._credentials_provider() is None:
            raise BrokerConnectionError("FYERS credentials unavailable; cannot open market-data WebSocket.")
        self._loop = asyncio.get_running_loop()
        self._running = True
        self.auth_failed = False
        self._reconnect_attempts = 0
        await self._open_socket()
        self._supervisor = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        self._running = False
        if self._supervisor:
            self._supervisor.cancel()
            try:
                await self._supervisor
            except (asyncio.CancelledError, Exception):
                pass
            self._supervisor = None
        await self._close_socket()
        self.ws_connected = False
        self.subscribed = False

    async def events(self) -> AsyncIterator[FeedEvent]:
        while self._running or not self._queue.empty():
            try:
                yield await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if not self._running:
                    return

    async def ticks(self) -> AsyncIterator[MarketTick]:
        async for ev in self.events():
            if ev.kind == "tick" and ev.tick is not None:
                yield ev.tick

    # ------------------------------------------------------------------
    # Socket management
    # ------------------------------------------------------------------
    async def _open_socket(self) -> None:
        creds = self._credentials_provider()
        if creds is None:
            self._mark_disconnected("NO_CREDENTIALS")
            self.auth_failed = True
            self._emit(FeedEvent("auth_failed", reason="NO_CREDENTIALS"))
            return
        client_id, token = creds
        self._reconnect_attempts += 1
        try:
            self._socket = self._socket_factory(
                access_token=f"{client_id}:{token}",
                on_message=self._on_message,
                on_error=self._on_error,
                on_connect=self._on_connect,
                on_close=self._on_close,
            )
            await asyncio.to_thread(self._socket.connect)
        except Exception as e:
            self._socket = None
            self._mark_disconnected(f"CONNECT_EXCEPTION:{type(e).__name__}")

    async def _close_socket(self) -> None:
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                await asyncio.to_thread(sock.close_connection)
            except Exception:
                pass

    async def _supervise(self) -> None:
        """Detect staleness/disconnects; reconnect with attempt limit (fail closed when exhausted)."""
        while self._running:
            await asyncio.sleep(1.0)
            now = self._now()
            if self.ws_connected and self.subscribed and not self.tick_fresh and not self._stale_reported:
                self._stale_reported = True
                self._emit(FeedEvent("stale", reason="NO_TICKS_WITHIN_STALE_WINDOW"))
            if self.auth_failed:
                continue
            if not self.ws_connected and self.disconnected_at is not None:
                if now - self.disconnected_at >= self.reconnect_after:
                    if self._reconnect_attempts >= self.max_reconnect_attempts:
                        continue
                    self.reconnect_count += 1
                    self.disconnected_at = now  # backoff window restarts
                    await self._close_socket()
                    await self._open_socket()

    # ------------------------------------------------------------------
    # SDK callbacks (run on SDK threads)
    # ------------------------------------------------------------------
    def _on_connect(self, *_: Any) -> None:
        with self._lock:
            self.ws_connected = True
            self.subscribed = False  # must be re-acknowledged after (re)connect
            self.last_connect_at = self._now()
            self.disconnected_at = None
            self._reconnect_attempts = 0
        try:
            if self._socket is not None:
                self._socket.subscribe(symbols=[self.symbol], data_type="SymbolUpdate")
        except Exception as e:
            self.last_error = f"SUBSCRIBE_EXCEPTION:{type(e).__name__}"

    def _on_close(self, *_: Any) -> None:
        self._mark_disconnected("SOCKET_CLOSED")

    def _on_error(self, message: Any = None) -> None:
        text = self._safe_text(message)
        self._mark_disconnected(f"SOCKET_ERROR:{text}")

    def _on_message(self, message: Any) -> None:
        if not isinstance(message, dict):
            return
        if "ltp" in message and "symbol" in message:
            self._handle_tick(message)
            return
        code = message.get("code")
        status = str(message.get("s", "")).lower()
        text = self._safe_text(message.get("message", ""))
        if code in (-300, -99, -100, -102, 401, 403) or ("token" in text.lower() and status == "error"):
            self.auth_failed = True
            self.last_error = f"AUTH_REJECTED:{text}"
            self._mark_disconnected("AUTH_REJECTED", emit_auth=True)
        elif status == "ok" and code == 200 and "subscri" in text.lower() and "unsub" not in text.lower():
            self.subscribed = True
        elif status == "error":
            self.last_error = f"FEED_ERROR:{code}:{text}"

    def _handle_tick(self, m: Dict[str, Any]) -> None:
        if m.get("symbol") != self.symbol:
            self.stats["ticks_ignored_other_symbol"] += 1
            return
        self.stats["raw_ticks_received"] += 1
        try:
            ltp = Decimal(str(m["ltp"]))
            if ltp <= 0:
                raise InvalidOperation
        except Exception:
            self.stats["ticks_rejected_bad_price"] += 1
            return
        epoch = m.get("exch_feed_time") or m.get("last_traded_time")
        try:
            epoch_i = int(epoch)
            if epoch_i <= 0:
                raise ValueError
            ex_ts = datetime.fromtimestamp(epoch_i, tz=timezone.utc)
        except Exception:
            self.stats["ticks_rejected_no_timestamp"] += 1
            return
        arrival = self._now()
        if ex_ts - arrival > self._max_future_skew:
            self.stats["ticks_rejected_future_timestamp"] += 1
            return
        self.stats["exchange_timestamps_valid"] += 1
        try:
            volume = int(m.get("vol_traded_today") or 0)
        except Exception:
            volume = 0
        tick = MarketTick(symbol=self.symbol, ltp=ltp, volume=max(volume, 0), timestamp=ex_ts)
        with self._lock:
            self.last_tick_received_at = arrival
            self.last_exchange_time = ex_ts
            self.last_ltp = ltp
            self._stale_reported = False
            self.stats["ticks_received"] += 1
        self._emit(FeedEvent("tick", tick=tick))

    # ------------------------------------------------------------------
    def _mark_disconnected(self, reason: str, emit_auth: bool = False) -> None:
        with self._lock:
            was_connected = self.ws_connected
            self.ws_connected = False
            self.subscribed = False
            if self.disconnected_at is None:
                self.disconnected_at = self._now()
            self.last_error = reason
        if was_connected or emit_auth:
            self._emit(FeedEvent("auth_failed" if emit_auth else "disconnect", reason=reason))

    def _emit(self, ev: FeedEvent) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        def _put() -> None:
            try:
                self._queue.put_nowait(ev)
            except asyncio.QueueFull:
                self.stats["queue_overflow_dropped"] += 1

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            pass

    def _safe_text(self, value: Any) -> str:
        text = str(value)
        try:
            creds = self._credentials_provider()
        except Exception:
            creds = None
        if creds:
            for secret in creds:
                if secret and len(secret) >= 6:
                    text = text.replace(secret, "***")
        return text[:200]
