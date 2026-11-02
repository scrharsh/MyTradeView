"""Base WebSocket service with exponential backoff, state tracking, and heartbeat monitoring."""

import asyncio
from datetime import datetime
from enum import Enum
import logging
import random
from typing import Callable, List, Optional

from src.common.time_utils import now_utc

logger = logging.getLogger(__name__)


class WebSocketConnectionState(str, Enum):
    """Lifecycle states of a WebSocket connection."""
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    SUBSCRIBED = "SUBSCRIBED"
    STALE = "STALE"
    ERROR = "ERROR"


class BaseWebSocketService:
    """
    Abstract base WebSocket service managing reconnection logic,
    state transitions, exponential backoff with jitter, and heartbeat tracking.
    """

    def __init__(
        self,
        name: str = "WebSocketService",
        initial_retry_delay: float = 1.0,
        max_retry_delay: float = 30.0,
        backoff_multiplier: float = 2.0,
        heartbeat_timeout_seconds: float = 15.0,
    ) -> None:
        self.name = name
        self.initial_retry_delay = initial_retry_delay
        self.max_retry_delay = max_retry_delay
        self.backoff_multiplier = backoff_multiplier
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds

        self._state: WebSocketConnectionState = WebSocketConnectionState.DISCONNECTED
        self._current_retry_delay: float = self.initial_retry_delay
        self._running: bool = False
        self._last_heartbeat_at: Optional[datetime] = None
        self._last_message_at: Optional[datetime] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._state_change_callbacks: List[Callable[[WebSocketConnectionState], None]] = []

    @property
    def state(self) -> WebSocketConnectionState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state in {
            WebSocketConnectionState.CONNECTED,
            WebSocketConnectionState.SUBSCRIBED,
        }

    @property
    def last_message_at(self) -> Optional[datetime]:
        return self._last_message_at

    @property
    def last_heartbeat_at(self) -> Optional[datetime]:
        return self._last_heartbeat_at

    def register_state_change_callback(self, cb: Callable[[WebSocketConnectionState], None]) -> None:
        """Register a callback invoked whenever connection state changes."""
        self._state_change_callbacks.append(cb)

    def _set_state(self, new_state: WebSocketConnectionState, reason: str = "") -> None:
        old_state = self._state
        if old_state == new_state:
            return
        self._state = new_state
        logger.info(f"[{self.name}] State transition: {old_state.value} -> {new_state.value}. {reason}")
        for cb in self._state_change_callbacks:
            try:
                cb(new_state)
            except Exception as e:
                logger.error(f"[{self.name}] Error in state change callback: {e}", exc_info=True)

    def record_heartbeat(self) -> None:
        """Record a successful heartbeat (ping/pong) event."""
        self._last_heartbeat_at = now_utc()
        self._last_message_at = self._last_heartbeat_at

    def record_message_received(self) -> None:
        """Record receipt of a valid incoming payload."""
        now = now_utc()
        self._last_message_at = now
        self._last_heartbeat_at = now
        # If we were previously STALE and receive fresh data, restore status
        if self._state == WebSocketConnectionState.STALE:
            self._set_state(WebSocketConnectionState.SUBSCRIBED, reason="Fresh message received")

    def calculate_next_backoff(self) -> float:
        """Calculate next backoff delay with 10% jitter."""
        jitter = random.uniform(0.9, 1.1)
        delay = min(self.max_retry_delay, self._current_retry_delay * jitter)
        self._current_retry_delay = min(
            self.max_retry_delay,
            self._current_retry_delay * self.backoff_multiplier,
        )
        return delay

    def reset_backoff(self) -> None:
        """Reset exponential backoff delay after successful connection."""
        self._current_retry_delay = self.initial_retry_delay

    async def start(self) -> None:
        """Start the WebSocket service and background tasks."""
        if self._running:
            return
        self._running = True
        self._set_state(WebSocketConnectionState.CONNECTING, reason="Service starting")
        await self._connect()

    async def stop(self) -> None:
        """Cleanly stop WebSocket service and cancel background monitors."""
        self._running = False
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        await self._disconnect()
        self._set_state(WebSocketConnectionState.DISCONNECTED, reason="Service stopped")

    async def _connect(self) -> None:
        """Hook for concrete implementations to establish connection."""
        try:
            success = await self._do_connect()
            if success:
                self.reset_backoff()
                self.record_heartbeat()
                self._set_state(WebSocketConnectionState.CONNECTED, reason="Connection established")
                await self._after_connected()
            else:
                self._set_state(WebSocketConnectionState.ERROR, reason="Connection attempt rejected")
                self._schedule_reconnect()
        except Exception as e:
            logger.error(f"[{self.name}] Connect failed: {e}", exc_info=True)
            self._set_state(WebSocketConnectionState.ERROR, reason=str(e))
            self._schedule_reconnect()

    async def _disconnect(self) -> None:
        """Hook for concrete implementations to tear down connection."""
        try:
            await self._do_disconnect()
        except Exception as e:
            logger.warning(f"[{self.name}] Error during disconnect: {e}")

    def _schedule_reconnect(self) -> None:
        """Schedule an asynchronous reconnect attempt using exponential backoff."""
        if not self._running:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        delay = self.calculate_next_backoff()
        logger.info(f"[{self.name}] Scheduling reconnect in {delay:.2f}s")
        self._reconnect_task = asyncio.create_task(self._reconnect_after(delay))

    async def _reconnect_after(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            if self._running:
                self._set_state(WebSocketConnectionState.CONNECTING, reason="Reconnecting")
                await self._connect()
        except asyncio.CancelledError:
            pass

    async def _do_connect(self) -> bool:
        """Subclass override to implement actual connection."""
        return True

    async def _do_disconnect(self) -> None:
        """Subclass override to implement actual disconnection."""
        pass

    async def _after_connected(self) -> None:
        """Hook executed immediately upon successful connection."""
        pass
