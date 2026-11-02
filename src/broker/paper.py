"""In-memory Paper Broker Client for deterministic simulation, offline development, and testing."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import uuid4

from src.broker.base import AbstractBrokerClient
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
from src.common.constants import OrderSide, OrderStatus, OrderType
from src.common.time_utils import now_utc
from src.core.models import MarketTick


class PaperBrokerClient(AbstractBrokerClient):
    """
    High-fidelity in-memory broker simulation.
    
    Provides deterministic simulation of:
    - Instant full and partial fills.
    - Configurable rejects and network timeouts.
    - Order modifications and cancellations.
    - Synchronized order-book, trade-book, and position state.
    """

    def __init__(
        self,
        initial_balance: Decimal = Decimal("100000.00"),
        force_timeout: bool = False,
        force_reject: bool = False,
        force_partial_fill: bool = False,
        auto_fill: bool = True,
        default_slippage: Decimal = Decimal("0.50"),
    ) -> None:
        self.initial_balance = initial_balance
        self.force_timeout = force_timeout
        self.force_reject = force_reject
        self.force_partial_fill = force_partial_fill
        self.auto_fill = auto_fill
        self.default_slippage = default_slippage
        self._reject_message: str = "Simulated RMS order rejection (Insufficient margin)"

        self._orders: Dict[str, BrokerOrderBookItem] = {}
        self._trades: List[BrokerTradeItem] = []
        self._positions: Dict[str, BrokerPosition] = {}
        self._balance = initial_balance
        self._used_margin = Decimal("0.00")
        self._status = BrokerConnectionStatus.CONNECTED

    @property
    def connection_status(self) -> BrokerConnectionStatus:
        return self._status

    @property
    def is_connected(self) -> bool:
        return self._status == BrokerConnectionStatus.CONNECTED

    def set_connection_status(self, status: BrokerConnectionStatus) -> None:
        self._status = status

    def set_force_timeout(self, timeout: bool) -> None:
        self.force_timeout = timeout

    def set_force_reject(self, reject: bool, message: str = "") -> None:
        self.force_reject = reject
        if message:
            self._reject_message = message

    def set_auto_fill(self, auto_fill: bool) -> None:
        self.auto_fill = auto_fill

    def get_trades_for_order(self, broker_order_id: str) -> List[BrokerTradeItem]:
        return [t for t in self._trades if t.broker_order_id == broker_order_id]

    async def authenticate(self) -> AuthStatus:
        return await self.check_auth()

    async def check_auth(self) -> AuthStatus:
        if self._status != BrokerConnectionStatus.CONNECTED:
            return AuthStatus(
                is_authenticated=False,
                app_id="PAPER_APP",
                message="Paper broker disconnected",
            )
        return AuthStatus(
            is_authenticated=True,
            app_id="PAPER_APP",
            user_id="PAPER_USER_01",
            user_name="Paper Trader",
            email="paper@mytradeview.internal",
            message="Authenticated in paper mode",
        )

    async def place_order(self, payload: BrokerOrderPayload) -> BrokerOrderResponse:
        """Process simulated order placement."""
        if self.force_timeout:
            return BrokerOrderResponse(
                success=False,
                client_order_id=payload.client_order_id,
                status_code=-999,
                message="Simulated broker network timeout",
                is_timeout_ambiguous=True,
            )

        if self.force_reject:
            return BrokerOrderResponse(
                success=False,
                client_order_id=payload.client_order_id,
                status_code=500,
                message=self._reject_message,
            )

        broker_id = f"P_ORD_{uuid4().hex[:10]}"

        if not self.auto_fill:
            order_item = BrokerOrderBookItem(
                broker_order_id=broker_id,
                client_order_id=payload.client_order_id,
                symbol=payload.symbol,
                qty=payload.qty,
                filled_qty=0,
                remaining_qty=payload.qty,
                status=OrderStatus.PENDING,
                order_type=payload.type,
                side=payload.side,
                avg_price=Decimal("0.00"),
                limit_price=payload.limit_price,
                order_timestamp=now_utc(),
                message="Open in paper simulation",
                tag=payload.tag,
            )
            self._orders[broker_id] = order_item
            return BrokerOrderResponse(
                success=True,
                broker_order_id=broker_id,
                client_order_id=payload.client_order_id,
                status_code=1101,
                message="Order submitted to paper book",
            )

        fill_price = payload.limit_price if payload.limit_price > 0 else Decimal("100.00")
        if payload.side == OrderSide.BUY:
            fill_price += self.default_slippage
        else:
            fill_price = max(Decimal("0.05"), fill_price - self.default_slippage)

        if not self.force_partial_fill:
            # Full fill immediately
            order_item = BrokerOrderBookItem(
                broker_order_id=broker_id,
                client_order_id=payload.client_order_id,
                symbol=payload.symbol,
                qty=payload.qty,
                filled_qty=payload.qty,
                remaining_qty=0,
                status=OrderStatus.FILLED,
                order_type=payload.type,
                side=payload.side,
                avg_price=fill_price,
                limit_price=payload.limit_price,
                order_timestamp=now_utc(),
                message="Filled in paper simulation",
                tag=payload.tag,
            )
            self._orders[broker_id] = order_item

            # Record trade
            trade = BrokerTradeItem(
                trade_id=f"P_TRD_{uuid4().hex[:8]}",
                broker_order_id=broker_id,
                symbol=payload.symbol,
                side=payload.side,
                traded_qty=payload.qty,
                traded_price=fill_price,
                trade_time=now_utc(),
            )
            self._trades.append(trade)

            # Update position
            self._update_position(payload.symbol, payload.side, payload.qty, fill_price)

        else:
            # Partial fill: 50%
            half = payload.qty // 2
            order_item = BrokerOrderBookItem(
                broker_order_id=broker_id,
                client_order_id=payload.client_order_id,
                symbol=payload.symbol,
                qty=payload.qty,
                filled_qty=half,
                remaining_qty=payload.qty - half,
                status=OrderStatus.PENDING,
                order_type=payload.type,
                side=payload.side,
                avg_price=fill_price,
                limit_price=payload.limit_price,
                order_timestamp=now_utc(),
                message="Partially filled in paper simulation",
                tag=payload.tag,
            )
            self._orders[broker_id] = order_item
            trade = BrokerTradeItem(
                trade_id=f"P_TRD_{uuid4().hex[:8]}",
                broker_order_id=broker_id,
                symbol=payload.symbol,
                side=payload.side,
                traded_qty=half,
                traded_price=fill_price,
                trade_time=now_utc(),
            )
            self._trades.append(trade)
            self._update_position(payload.symbol, payload.side, half, fill_price)

        return BrokerOrderResponse(
            success=True,
            broker_order_id=broker_id,
            client_order_id=payload.client_order_id,
            status_code=1101,
            message="Order placed successfully in paper mode",
        )

    def _update_position(self, symbol: str, side: OrderSide, qty: int, price: Decimal) -> None:
        pos = self._positions.get(symbol)
        if pos is None:
            buy_q = qty if side == OrderSide.BUY else 0
            sell_q = qty if side == OrderSide.SELL else 0
            net_q = buy_q - sell_q
            buy_a = price if side == OrderSide.BUY else Decimal("0.00")
            sell_a = price if side == OrderSide.SELL else Decimal("0.00")
            self._positions[symbol] = BrokerPosition(
                symbol=symbol,
                net_qty=net_q,
                buy_qty=buy_q,
                sell_qty=sell_q,
                buy_avg=buy_a,
                sell_avg=sell_a,
                realized_pnl=Decimal("0.00"),
                unrealized_pnl=Decimal("0.00"),
            )
        else:
            if side == OrderSide.BUY:
                new_buy_q = pos.buy_qty + qty
                new_buy_val = (pos.buy_avg * Decimal(pos.buy_qty)) + (price * Decimal(qty))
                new_avg = (new_buy_val / Decimal(new_buy_q)).quantize(Decimal("0.01"))
                self._positions[symbol] = BrokerPosition(
                    symbol=symbol,
                    net_qty=pos.net_qty + qty,
                    buy_qty=new_buy_q,
                    sell_qty=pos.sell_qty,
                    buy_avg=new_avg,
                    sell_avg=pos.sell_avg,
                    realized_pnl=pos.realized_pnl,
                    unrealized_pnl=pos.unrealized_pnl,
                )
            else:
                new_sell_q = pos.sell_qty + qty
                new_sell_val = (pos.sell_avg * Decimal(pos.sell_qty)) + (price * Decimal(qty))
                new_avg = (new_sell_val / Decimal(new_sell_q)).quantize(Decimal("0.01"))
                # Realized PnL on closed portion
                realized = (price - pos.buy_avg) * Decimal(qty)
                self._positions[symbol] = BrokerPosition(
                    symbol=symbol,
                    net_qty=pos.net_qty - qty,
                    buy_qty=pos.buy_qty,
                    sell_qty=new_sell_q,
                    buy_avg=pos.buy_avg,
                    sell_avg=new_avg,
                    realized_pnl=pos.realized_pnl + realized,
                    unrealized_pnl=pos.unrealized_pnl,
                )

    async def modify_order(
        self,
        broker_order_id: str,
        qty: Optional[int] = None,
        limit_price: Optional[Decimal] = None,
    ) -> BrokerOrderResponse:
        order = self._orders.get(broker_order_id)
        if not order:
            return BrokerOrderResponse(
                success=False,
                broker_order_id=broker_order_id,
                client_order_id=broker_order_id,
                status_code=404,
                message="Order not found",
            )
        # Update order item
        new_qty = qty if qty is not None else order.qty
        new_lp = limit_price if limit_price is not None else order.limit_price
        self._orders[broker_order_id] = BrokerOrderBookItem(
            broker_order_id=order.broker_order_id,
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            qty=new_qty,
            filled_qty=order.filled_qty,
            remaining_qty=max(0, new_qty - order.filled_qty),
            status=order.status,
            order_type=order.order_type,
            side=order.side,
            avg_price=order.avg_price,
            limit_price=new_lp,
            order_timestamp=now_utc(),
            message="Modified in paper mode",
            tag=order.tag,
        )
        return BrokerOrderResponse(
            success=True,
            broker_order_id=broker_order_id,
            client_order_id=order.client_order_id or broker_order_id,
            status_code=1102,
            message="Order modified",
        )

    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResponse:
        order = self._orders.get(broker_order_id)
        if not order:
            return BrokerOrderResponse(
                success=False,
                broker_order_id=broker_order_id,
                client_order_id=broker_order_id,
                status_code=404,
                message="Order not found",
            )
        if order.status in {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}:
            return BrokerOrderResponse(
                success=False,
                broker_order_id=broker_order_id,
                client_order_id=order.client_order_id or broker_order_id,
                status_code=400,
                message=f"Order is already in terminal status: {order.status.name}",
            )
        self._orders[broker_order_id] = BrokerOrderBookItem(
            broker_order_id=order.broker_order_id,
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            qty=order.qty,
            filled_qty=order.filled_qty,
            remaining_qty=0,
            status=OrderStatus.CANCELLED,
            order_type=order.order_type,
            side=order.side,
            avg_price=order.avg_price,
            limit_price=order.limit_price,
            order_timestamp=now_utc(),
            message="Cancelled in paper mode",
            tag=order.tag,
        )
        return BrokerOrderResponse(
            success=True,
            broker_order_id=broker_order_id,
            client_order_id=order.client_order_id or broker_order_id,
            status_code=1103,
            message="Order cancelled",
        )

    async def get_order_book(self) -> List[BrokerOrderBookItem]:
        return list(self._orders.values())

    async def get_trade_book(self) -> List[BrokerTradeItem]:
        return list(self._trades)

    async def get_positions(self) -> List[BrokerPosition]:
        return list(self._positions.values())

    async def get_funds(self) -> BrokerFunds:
        return BrokerFunds(
            total_balance=self._balance,
            available_balance=self._balance - self._used_margin,
            used_margin=self._used_margin,
        )

    async def get_quotes(self, symbols: List[str]) -> List[MarketTick]:
        now = now_utc()
        return [
            MarketTick(
                symbol=s,
                ltp=Decimal("25000.00"),
                volume=10000,
                timestamp=now,
                bid_price=Decimal("24999.50"),
                ask_price=Decimal("25000.50"),
            )
            for s in symbols
        ]
