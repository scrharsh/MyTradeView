"""WebSocket telemetry manager for real-time frontend streaming.

Streams:
- Candlestick / tick updates
- Live MTM snapshots and drawdown
- Order lifecycle state updates
- Signal events
- Risk / circuit breaker alerts
"""

import asyncio
from datetime import datetime
import json
import logging
from typing import Any, Dict, List, Set
from fastapi import WebSocket, WebSocketDisconnect

from src.common.time_utils import now_utc

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket client connections and fan-out broadcasting."""

    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register client connection."""
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
        logger.info("WebSocket client connected. Total clients: %d", len(self.active_connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        """Unregister client connection."""
        async with self._lock:
            self.active_connections.discard(websocket)
        logger.info("WebSocket client disconnected. Total clients: %d", len(self.active_connections))

    async def broadcast(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Broadcast JSON message to all connected clients."""
        if not self.active_connections:
            return

        message = json.dumps(
            {
                "event": event_type,
                "timestamp": now_utc().isoformat(),
                "data": payload,
            },
            default=str,
        )

        disconnected: List[WebSocket] = []
        async with self._lock:
            for connection in list(self.active_connections):
                try:
                    await connection.send_text(message)
                except Exception as exc:
                    logger.debug("Failed sending to client: %s", exc)
                    disconnected.append(connection)

            for dead_conn in disconnected:
                self.active_connections.discard(dead_conn)


ws_manager = ConnectionManager()

