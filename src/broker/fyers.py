"""FYERS API v3 Broker Client implementation with response normalization, redaction, and safety interlocks."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional

from config.broker_config import BrokerConfig
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
    redact_sensitive_dict,
)
from src.common.constants import ExecutionMode, OrderSide, OrderStatus, OrderType
from src.common.exceptions import (
    BrokerAuthenticationError,
    BrokerConnectionError,
    BrokerError,
    BrokerOrderRejectedError,
)
from src.core.models import MarketTick

logger = logging.getLogger(__name__)


class FyersBrokerClient(AbstractBrokerClient):
    """
    Official FYERS API v3 broker implementation.
    
    Adheres strictly to:
    - Zero live executions in PAPER mode (fail-safe enforcement).
    - Ambiguous timeout handling without blind resubmission.
    - Automatic token and credential redaction across all logs.
    - Normalization of raw FYERS dictionaries into typed domain models.
    """

    def __init__(
        self,
        client: Optional[Any] = None,
        config: Optional[BrokerConfig] = None,
        execution_mode: ExecutionMode = ExecutionMode.PAPER,
        app_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> None:
        self._client = client
        self.config = config or BrokerConfig()
        if app_id:
            self.config.client_id = app_id
        self._access_token = access_token
        self.execution_mode = execution_mode
        self._connection_status: BrokerConnectionStatus = (
            BrokerConnectionStatus.CONNECTED if (client is not None or access_token is not None) else BrokerConnectionStatus.DISCONNECTED
        )

    @property
    def connection_status(self) -> BrokerConnectionStatus:
        return self._connection_status

    @property
    def auth_status(self) -> AuthStatus:
        return AuthStatus(
            is_authenticated=(self._connection_status == BrokerConnectionStatus.CONNECTED),
            app_id=self.config.client_id or "UNKNOWN",
            message=self._connection_status.value,
        )

    async def _make_request(self, endpoint: str, method: str = "GET", data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute request via FYERS SDK client instance."""
        if self._client is not None and hasattr(self._client, "get_profile") and endpoint == "/api/v3/profile":
            return await asyncio.to_thread(self._client.get_profile)
        return {"s": "error", "code": -1, "message": "No client configured"}

    # --------------------------------------------------------------------------
    # Authentication & Profile
    # --------------------------------------------------------------------------

    async def authenticate(self) -> AuthStatus:
        """Alias for check_auth()."""
        return await self.check_auth()

    async def check_auth(self) -> AuthStatus:
        """Validate access token and inspect authenticated profile."""
        if self._client is None and not self._access_token:
            self._connection_status = BrokerConnectionStatus.DISCONNECTED
            return AuthStatus(
                is_authenticated=False,
                app_id=self.config.client_id or "UNKNOWN",
                message="Broker client is unconfigured or offline",
            )

        try:
            response = await self._make_request("/api/v3/profile")
            safe_resp = redact_sensitive_dict(response)
            logger.debug(f"[Broker Auth] Profile response: {safe_resp}")

            if not response or response.get("s") != "ok":
                code = response.get("code") if response else -1
                msg = response.get("message", "Profile check failed")
                if code in (-15, -16):
                    self._connection_status = BrokerConnectionStatus.AUTH_EXPIRED
                    raise BrokerAuthenticationError(f"Token expired (Code {code}): {msg}")

                self._connection_status = BrokerConnectionStatus.ERROR
                return AuthStatus(
                    is_authenticated=False,
                    app_id=self.config.client_id or "UNKNOWN",
                    message=f"Auth error (Code {code}): {msg}",
                )

            data = response.get("data", {})
            self._connection_status = BrokerConnectionStatus.CONNECTED
            return AuthStatus(
                is_authenticated=True,
                app_id=self.config.client_id or "FYERS_APP",
                user_id=data.get("fy_id"),
                user_name=data.get("name"),
                email=data.get("email_id") or data.get("email"),
                message="Authenticated successfully",
            )
        except BrokerAuthenticationError:
            raise
        except Exception as e:
            self._connection_status = BrokerConnectionStatus.ERROR
            logger.error(f"[Broker Auth] Network error validating profile: {e}")
            raise BrokerConnectionError(f"Failed to connect to FYERS authentication endpoint: {e}")

    # --------------------------------------------------------------------------
    # Order Lifecycle Execution
    # --------------------------------------------------------------------------

    async def place_order(self, payload: BrokerOrderPayload) -> BrokerOrderResponse:
        """
        Submit a new order via FYERS /api/v3/orders/sync.
        
        CRITICAL: If execution_mode is PAPER, real network submission is strictly blocked.
        If a network timeout occurs, it is flagged as ambiguous so OMS reconciles
        before any retry is considered.
        """
        # Hard safety barrier
        if self.execution_mode != ExecutionMode.LIVE:
            logger.info(
                f"[PAPER MODE] Real broker order blocked for {payload.client_order_id}. "
                f"Payload: {payload.symbol} {payload.side.name} {payload.qty} Qty"
            )
            return BrokerOrderResponse(
                success=True,
                broker_order_id=f"PAPER_ORD_{payload.client_order_id}",
                client_order_id=payload.client_order_id,
                status_code=1101,
                message="Order simulated successfully in PAPER mode",
            )

        if self._client is None:
            raise BrokerConnectionError("Cannot place order: FYERS client is unconfigured or offline")

        fyers_payload = payload.to_fyers_dict()
        safe_payload = redact_sensitive_dict(fyers_payload)
        logger.info(f"[Broker Order] Submitting order {payload.client_order_id}: {safe_payload}")

        try:
            # FYERS place_order endpoint (/api/v3/orders/sync)
            response = await asyncio.to_thread(self._client.place_order, data=fyers_payload)
            safe_resp = redact_sensitive_dict(response)
            logger.info(f"[Broker Order] Response for {payload.client_order_id}: {safe_resp}")

            if not response or response.get("s") != "ok":
                code = response.get("code") if response else -1
                msg = response.get("message", "Order rejected by broker")
                return BrokerOrderResponse(
                    success=False,
                    broker_order_id=None,
                    client_order_id=payload.client_order_id,
                    status_code=code,
                    message=msg,
                    raw_response=safe_resp,
                )

            broker_id = str(response.get("id"))
            return BrokerOrderResponse(
                success=True,
                broker_order_id=broker_id,
                client_order_id=payload.client_order_id,
                status_code=response.get("code", 1101),
                message=response.get("message", "Order placed successfully"),
                raw_response=safe_resp,
            )

        except asyncio.TimeoutError:
            logger.critical(
                f"[Broker Order TIMEOUT] Request timed out for {payload.client_order_id}. "
                "Order status ambiguous! Must reconcile before resubmitting."
            )
            return BrokerOrderResponse(
                success=False,
                client_order_id=payload.client_order_id,
                status_code=-999,
                message="Network timeout during order submission. Outcome ambiguous.",
                is_timeout_ambiguous=True,
            )
        except Exception as e:
            logger.error(f"[Broker Order Error] Failed to submit {payload.client_order_id}: {e}")
            raise BrokerConnectionError(f"Network error communicating with FYERS order endpoint: {e}")

    async def modify_order(
        self,
        broker_order_id: str,
        qty: Optional[int] = None,
        limit_price: Optional[Decimal] = None,
    ) -> BrokerOrderResponse:
        """Modify open order via FYERS PUT /api/v3/orders/sync."""
        if self.execution_mode != ExecutionMode.LIVE:
            return BrokerOrderResponse(
                success=True,
                broker_order_id=broker_order_id,
                client_order_id=broker_order_id,
                status_code=1102,
                message="Order modified in PAPER mode",
            )

        if self._client is None:
            raise BrokerConnectionError("FYERS client is not configured")

        mod_data: Dict[str, Any] = {"id": broker_order_id}
        if qty is not None:
            mod_data["qty"] = qty
        if limit_price is not None:
            mod_data["limitPrice"] = float(limit_price)

        try:
            response = await asyncio.to_thread(self._client.modify_order, data=mod_data)
            success = response.get("s") == "ok" if response else False
            return BrokerOrderResponse(
                success=success,
                broker_order_id=broker_order_id,
                client_order_id=broker_order_id,
                status_code=response.get("code", 200) if response else -1,
                message=response.get("message", "") if response else "Empty response",
                raw_response=redact_sensitive_dict(response or {}),
            )
        except Exception as e:
            raise BrokerConnectionError(f"Failed to modify order {broker_order_id}: {e}")

    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResponse:
        """Cancel open order via FYERS DELETE /api/v3/orders/sync."""
        if self.execution_mode != ExecutionMode.LIVE:
            return BrokerOrderResponse(
                success=True,
                broker_order_id=broker_order_id,
                client_order_id=broker_order_id,
                status_code=1103,
                message="Order cancelled in PAPER mode",
            )

        if self._client is None:
            raise BrokerConnectionError("FYERS client is not configured")

        try:
            response = await asyncio.to_thread(self._client.cancel_order, data={"id": broker_order_id})
            success = response.get("s") == "ok" if response else False
            return BrokerOrderResponse(
                success=success,
                broker_order_id=broker_order_id,
                client_order_id=broker_order_id,
                status_code=response.get("code", 200) if response else -1,
                message=response.get("message", "") if response else "Empty response",
                raw_response=redact_sensitive_dict(response or {}),
            )
        except Exception as e:
            raise BrokerConnectionError(f"Failed to cancel order {broker_order_id}: {e}")

    # --------------------------------------------------------------------------
    # Queries & Reconciliation Data Sources
    # --------------------------------------------------------------------------

    async def get_order_book(self) -> List[BrokerOrderBookItem]:
        """Fetch order book from FYERS /api/v3/orders and normalize."""
        if self._client is None:
            return []

        try:
            response = await asyncio.to_thread(self._client.orderbook)
            if not response or response.get("s") != "ok":
                logger.warning(f"[Broker] Failed to fetch order book: {response}")
                return []

            items: List[BrokerOrderBookItem] = []
            for raw in response.get("orderBook", []):
                # Status normalization
                status_val = int(raw.get("status", 6))
                try:
                    status = OrderStatus(status_val)
                except ValueError:
                    status = OrderStatus.PENDING

                # Side normalization
                side_val = int(raw.get("side", 1))
                side = OrderSide.BUY if side_val == 1 else OrderSide.SELL

                # Type normalization
                type_val = int(raw.get("type", 2))
                try:
                    otype = OrderType(type_val)
                except ValueError:
                    otype = OrderType.MARKET

                # Timestamp
                dt_str = raw.get("orderDateTime")
                if dt_str:
                    try:
                        ts = datetime.strptime(dt_str, "%d-%b-%Y %H:%M:%S").replace(tzinfo=timezone.utc)
                    except Exception:
                        ts = now_utc()
                else:
                    ts = now_utc()

                items.append(
                    BrokerOrderBookItem(
                        broker_order_id=str(raw.get("id")),
                        client_order_id=raw.get("tag"),
                        symbol=raw.get("symbol", ""),
                        qty=int(raw.get("qty", 0)),
                        filled_qty=int(raw.get("filledQty", 0)),
                        remaining_qty=int(raw.get("remainingQuantity", 0)),
                        status=status,
                        order_type=otype,
                        side=side,
                        avg_price=Decimal(str(raw.get("tradedPrice", 0.0))),
                        limit_price=Decimal(str(raw.get("limitPrice", 0.0))),
                        order_timestamp=ts,
                        message=raw.get("message", ""),
                        tag=raw.get("tag"),
                    )
                )
            return items
        except Exception as e:
            logger.error(f"[Broker] Error retrieving order book: {e}")
            raise BrokerConnectionError(f"Order book retrieval failed: {e}")

    async def get_trade_book(self) -> List[BrokerTradeItem]:
        """Fetch trade book from FYERS /api/v3/tradebook and normalize."""
        if self._client is None:
            return []

        try:
            response = await asyncio.to_thread(self._client.tradebook)
            if not response or response.get("s") != "ok":
                return []

            trades: List[BrokerTradeItem] = []
            for raw in response.get("tradeBook", []):
                side_val = int(raw.get("side", 1))
                side = OrderSide.BUY if side_val == 1 else OrderSide.SELL

                dt_str = raw.get("orderDateTime")
                try:
                    ts = datetime.strptime(dt_str, "%d-%b-%Y %H:%M:%S").replace(tzinfo=timezone.utc) if dt_str else now_utc()
                except Exception:
                    ts = now_utc()

                trades.append(
                    BrokerTradeItem(
                        trade_id=str(raw.get("tradeNumber", raw.get("tradeNo", ""))),
                        broker_order_id=str(raw.get("orderId", raw.get("orderNumber", ""))),
                        symbol=raw.get("symbol", ""),
                        side=side,
                        traded_qty=int(raw.get("tradedQty", 0)),
                        traded_price=Decimal(str(raw.get("tradePrice", 0.0))),
                        trade_time=ts,
                        product_type=raw.get("productType", "INTRADAY"),
                    )
                )
            return trades
        except Exception as e:
            logger.error(f"[Broker] Error retrieving trade book: {e}")
            raise BrokerConnectionError(f"Trade book retrieval failed: {e}")

    async def get_positions(self) -> List[BrokerPosition]:
        """Fetch positions from FYERS /api/v3/positions and normalize."""
        if self._client is None:
            return []

        try:
            response = await asyncio.to_thread(self._client.positions)
            if not response or response.get("s") != "ok":
                return []

            positions: List[BrokerPosition] = []
            for raw in response.get("netPositions", []):
                positions.append(
                    BrokerPosition(
                        symbol=raw.get("symbol", ""),
                        net_qty=int(raw.get("netQty", 0)),
                        buy_qty=int(raw.get("buyQty", 0)),
                        sell_qty=int(raw.get("sellQty", 0)),
                        buy_avg=Decimal(str(raw.get("buyAvg", 0.0))),
                        sell_avg=Decimal(str(raw.get("sellAvg", 0.0))),
                        realized_pnl=Decimal(str(raw.get("realized_profit", 0.0))),
                        unrealized_pnl=Decimal(str(raw.get("unrealized_profit", 0.0))),
                        product_type=raw.get("productType", "INTRADAY"),
                    )
                )
            return positions
        except Exception as e:
            logger.error(f"[Broker] Error retrieving positions: {e}")
            raise BrokerConnectionError(f"Positions retrieval failed: {e}")

    async def get_funds(self) -> BrokerFunds:
        """Fetch fund balances from FYERS /api/v3/funds."""
        if self._client is None:
            return BrokerFunds(total_balance=Decimal("0.00"), available_balance=Decimal("0.00"), used_margin=Decimal("0.00"))

        try:
            response = await asyncio.to_thread(self._client.funds)
            if not response or response.get("s") != "ok":
                return BrokerFunds(total_balance=Decimal("0.00"), available_balance=Decimal("0.00"), used_margin=Decimal("0.00"))

            tot = Decimal("0.00")
            avail = Decimal("0.00")
            for item in response.get("fund_limit", []):
                title = item.get("title", "")
                amt = Decimal(str(item.get("equityAmount", 0.0)))
                if "Total Balance" in title:
                    tot = amt
                elif "Available Balance" in title:
                    avail = amt

            return BrokerFunds(
                total_balance=tot,
                available_balance=avail,
                used_margin=max(Decimal("0.00"), tot - avail),
            )
        except Exception as e:
            logger.error(f"[Broker] Error retrieving funds: {e}")
            raise BrokerConnectionError(f"Funds retrieval failed: {e}")

    async def get_quotes(self, symbols: List[str]) -> List[MarketTick]:
        """Fetch quotes via /api/v3/quotes."""
        if not symbols or self._client is None:
            return []

        sym_str = ",".join(symbols)
        try:
            response = await asyncio.to_thread(self._client.quotes, data={"symbols": sym_str})
            if not response or response.get("s") != "ok":
                return []

            ticks: List[MarketTick] = []
            for item in response.get("d", []):
                v = item.get("v", {})
                lp = v.get("lp")
                if lp:
                    ticks.append(
                        MarketTick(
                            symbol=item.get("n", ""),
                            ltp=Decimal(str(lp)),
                            volume=int(v.get("volume", 0)),
                            timestamp=now_utc(),
                            bid_price=Decimal(str(v.get("bid"))) if v.get("bid") is not None else None,
                            ask_price=Decimal(str(v.get("ask"))) if v.get("ask") is not None else None,
                        )
                    )
            return ticks
        except Exception as e:
            raise BrokerConnectionError(f"Quotes fetch failed: {e}")
