"""Authenticated FYERS order-update WebSocket lifecycle wrapper."""

import asyncio
from typing import Any, Callable, Optional


class FyersOrderFeed:
    """Run the official FYERS order socket and dispatch normalized frames."""

    def __init__(
        self,
        credentials_provider: Callable[[], Optional[tuple[str, str]]],
        on_frame: Callable[[dict[str, Any]], Any],
        socket_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._credentials_provider = credentials_provider
        self._on_frame = on_frame
        self._socket_factory = socket_factory or self._default_socket_factory
        self._socket: Any = None
        self._running = False

    @staticmethod
    def _default_socket_factory(**kwargs: Any) -> Any:
        from fyers_apiv3.FyersWebsocket import order_ws

        return order_ws.FyersOrderSocket(**kwargs)

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        creds = self._credentials_provider()
        if creds is None:
            raise RuntimeError("FYERS credentials unavailable; order feed cannot start.")
        client_id, token = creds
        self._socket = self._socket_factory(
            access_token=f"{client_id}:{token}",
            on_orders=self._on_frame,
            on_trades=self._on_frame,
            on_positions=self._on_frame,
            on_general=self._on_frame,
            on_error=lambda _msg: None,
            on_connect=lambda: None,
            on_close=lambda: None,
            reconnect=True,
        )
        self._running = True
        await asyncio.to_thread(self._socket.connect)

    async def stop(self) -> None:
        self._running = False
        socket, self._socket = self._socket, None
        if socket is not None:
            close = getattr(socket, "close_connection", None)
            if close is not None:
                await asyncio.to_thread(close)
