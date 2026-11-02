"""Order update WebSocket service parsing FYERS frames and routing lifecycle events to the OMS."""

from datetime import datetime
from decimal import Decimal
import logging
from typing import Any, Callable, Dict, List, Optional, Set
from uuid import uuid4

from src.broker.models import BrokerOrderBookItem, BrokerTradeItem
from src.common.constants import OrderSide, OrderStatus, OrderType, ProductType
from src.common.time_utils import now_utc
from src.core.models import Fill
from src.oms.manager import OrderManagementSystem
from src.oms.models import OmsOrderStatus, map_fyers_status_to_oms
from src.websocket.base import BaseWebSocketService, WebSocketConnectionState

logger = logging.getLogger(__name__)


class OrderUpdateWebSocketService(BaseWebSocketService):
    """
    Subscribes to broker order and trade execution events, normalizes frames,
    deduplicates execution messages, and dispatches fills and status updates to the OMS.
    """

    def __init__(
        self,
        oms: OrderManagementSystem,
        access_token: Optional[str] = None,
        initial_retry_delay: float = 1.0,
        max_retry_delay: float = 30.0,
    ) -> None:
        super().__init__(
            name="OrderUpdateWebSocket",
            initial_retry_delay=initial_retry_delay,
            max_retry_delay=max_retry_delay,
        )
        self.oms = oms
        self.access_token = access_token
        self._processed_trade_ids: Set[str] = set()
        self._order_callbacks: List[Callable[[Dict[str, Any]], None]] = []

    def register_order_callback(self, cb: Callable[[Dict[str, Any]], None]) -> None:
        """Register external listener for raw normalized order events."""
        self._order_callbacks.append(cb)

    async def process_incoming_frame(self, raw_frame: Dict[str, Any]) -> None:
        """
        Ingest, normalize, and dispatch an order or trade frame from FYERS.
        Frame may contain 'orders' dict/list or 'trades' dict/list.
        """
        self.record_message_received()

        # Handle 'orders' frame
        orders_data = raw_frame.get("orders") or raw_frame.get("order")
        if orders_data:
            if isinstance(orders_data, list):
                for ord_item in orders_data:
                    await self._handle_order_update(ord_item)
            elif isinstance(orders_data, dict):
                await self._handle_order_update(orders_data)

        # Handle 'trades' frame
        trades_data = raw_frame.get("trades") or raw_frame.get("trade")
        if trades_data:
            if isinstance(trades_data, list):
                for trd_item in trades_data:
                    await self._handle_trade_execution(trd_item)
            elif isinstance(trades_data, dict):
                await self._handle_trade_execution(trades_data)

        for cb in self._order_callbacks:
            try:
                cb(raw_frame)
            except Exception as e:
                logger.error(f"[{self.name}] Callback error: {e}", exc_info=True)

    async def _handle_order_update(self, item: Dict[str, Any]) -> None:
        """Handle individual order status update dictionary."""
        broker_order_id = str(item.get("id", ""))
        client_order_id = str(item.get("customOrderNum") or item.get("tag") or "")
        status_val = item.get("status")
        if status_val is None:
            return

        try:
            fyers_status = OrderStatus(int(status_val))
        except (ValueError, TypeError):
            logger.warning(f"[{self.name}] Unknown FYERS status: {status_val}")
            return

        filled_qty = int(item.get("filledQty", 0))
        traded_price = Decimal(str(item.get("tradedPrice", 0)))
        message = str(item.get("message") or "")

        # Resolve client_order_id if not present in customOrderNum
        if not client_order_id and broker_order_id:
            ord_obj = self.oms.get_order_by_broker_id(broker_order_id)
            if ord_obj:
                client_order_id = ord_obj.client_order_id

        if not client_order_id:
            logger.warning(f"[{self.name}] Cannot associate broker order {broker_order_id} with OMS order")
            return

        # If there is a fill and no separate trade event, generate a synthetic Fill
        existing = self.oms.get_order(client_order_id)
        if existing and filled_qty > existing.filled_qty and traded_price > 0:
            delta_qty = filled_qty - existing.filled_qty
            fill_id = f"FILL_WS_{broker_order_id}_{filled_qty}"
            if fill_id not in self._processed_trade_ids:
                self._processed_trade_ids.add(fill_id)
                fill = Fill(
                    fill_id=fill_id,
                    client_order_id=client_order_id,
                    broker_order_id=broker_order_id,
                    symbol=existing.symbol,
                    side=existing.side,
                    fill_price=traded_price,
                    fill_qty=delta_qty,
                    timestamp=now_utc(),
                )
                await self.oms.on_fill_event(fill)

        # Update order state
        self.oms.on_broker_order_update(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            broker_status=fyers_status,
            message=message,
        )

    async def _handle_trade_execution(self, item: Dict[str, Any]) -> None:
        """Handle individual trade execution dictionary."""
        trade_id = str(item.get("tradeNumber") or item.get("tradeId") or item.get("id") or uuid4().hex[:10])
        if trade_id in self._processed_trade_ids:
            logger.debug(f"[{self.name}] Ignoring duplicate trade frame: {trade_id}")
            return
        self._processed_trade_ids.add(trade_id)

        broker_order_id = str(item.get("orderNumber") or item.get("orderId") or "")
        client_order_id = str(item.get("customOrderNum") or "")
        symbol = str(item.get("symbol", ""))
        traded_price = Decimal(str(item.get("tradedPrice", item.get("price", 0))))
        traded_qty = int(item.get("tradedQty", item.get("qty", 0)))

        # Resolve OMS order
        ord_obj = None
        if client_order_id:
            ord_obj = self.oms.get_order(client_order_id)
        if not ord_obj and broker_order_id:
            ord_obj = self.oms.get_order_by_broker_id(broker_order_id)

        if not ord_obj:
            logger.warning(f"[{self.name}] Trade execution received for unknown order: {broker_order_id} / {client_order_id}")
            return

        fill = Fill(
            fill_id=f"TRD_{trade_id}",
            client_order_id=ord_obj.client_order_id,
            broker_order_id=broker_order_id,
            symbol=symbol or ord_obj.symbol,
            side=ord_obj.side,
            fill_price=traded_price,
            fill_qty=traded_qty,
            timestamp=now_utc(),
        )
        await self.oms.on_fill_event(fill)
