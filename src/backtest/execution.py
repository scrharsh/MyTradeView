"""Simulated Execution Model for Backtesting with realistic costs and lookahead prevention."""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import logging
from typing import List, Optional, Tuple

from src.backtest.models import ExecutionModelConfig
from src.common.constants import OrderSide
from src.core.models import Candle, Fill, OrderRequest

logger = logging.getLogger(__name__)


class SimulatedExecutionModel:
    """
    Applies realistic execution slippage, statutory taxes, and fill timing to simulated orders.
    Guarantees strict prevention of look-ahead bias and double-counting of costs.
    """

    def __init__(self, config: Optional[ExecutionModelConfig] = None) -> None:
        self.config = config or ExecutionModelConfig()

    def calculate_slippage_price(self, side: OrderSide, base_price: Decimal) -> Decimal:
        """
        Apply slippage: Adds slippage on BUY, subtracts on SELL.
        """
        if self.config.slippage_model == "percentage":
            slip_amount = (base_price * self.config.slippage_points / Decimal("100.00")).quantize(Decimal("0.05"))
        else:
            slip_amount = self.config.slippage_points

        if side == OrderSide.BUY:
            return (base_price + slip_amount).quantize(Decimal("0.05"), rounding=ROUND_HALF_UP)
        else:
            fill_p = base_price - slip_amount
            # Options price cannot slip below tick size (0.05)
            return max(Decimal("0.05"), fill_p).quantize(Decimal("0.05"), rounding=ROUND_HALF_UP)

    def calculate_order_taxes(self, side: OrderSide, price: Decimal, qty: int) -> Decimal:
        """
        Calculate complete statutory and broker charges for a single order leg:
        - Flat Brokerage
        - Securities Transaction Tax (STT on SELL side)
        - Exchange Turnover Charges (NSE)
        - GST on (Brokerage + Exchange Turnover)
        - Stamp Duty (on BUY side)
        - SEBI Regulatory Fees
        """
        turnover = price * Decimal(qty)
        brokerage = self.config.brokerage_per_order
        turnover_charges = turnover * self.config.exchange_turnover_rate
        gst = (brokerage + turnover_charges) * self.config.gst_rate
        sebi_fees = turnover * self.config.sebi_charges_rate

        if side == OrderSide.BUY:
            stt = Decimal("0.00")
            stamp_duty = turnover * self.config.stamp_duty_rate
        else:
            stt = turnover * self.config.stt_rate
            stamp_duty = Decimal("0.00")

        total_tax = brokerage + turnover_charges + gst + sebi_fees + stt + stamp_duty
        return total_tax.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def calculate_roundtrip_costs(
        self,
        entry_price: Decimal,
        exit_price: Decimal,
        qty: int,
    ) -> Decimal:
        """Calculate complete statutory roundtrip trading costs (entry BUY + exit SELL)."""
        buy_costs = self.calculate_order_taxes(OrderSide.BUY, entry_price, qty)
        sell_costs = self.calculate_order_taxes(OrderSide.SELL, exit_price, qty)
        return (buy_costs + sell_costs).quantize(Decimal("0.01"))

    def simulate_fill(
        self,
        order_request: OrderRequest,
        candle: Candle,
        is_next_bar: bool = False,
    ) -> List[Fill]:
        """
        Simulate order fill against an option candlestick bar.
        
        Args:
            order_request: Validated outbound order request.
            candle: Market candle for the target contract.
            is_next_bar: True if evaluating the bar following order generation (next_bar_open).
            
        Returns:
            List of generated Fill reports (1 or more if partial fill enabled).
        """
        if self.config.fill_timing == "next_bar_open" and not is_next_bar:
            # Order generated on current bar, fill is deferred to next bar's open
            return []

        base_price = candle.open if (self.config.fill_timing == "next_bar_open" and is_next_bar) else candle.close
        fill_price = self.calculate_slippage_price(order_request.side, base_price)

        if not self.config.partial_fill_supported:
            fill = Fill(
                client_order_id=order_request.client_order_id,
                broker_order_id=f"SIM_ORD_{order_request.client_order_id}",
                symbol=order_request.symbol,
                side=order_request.side,
                fill_price=fill_price,
                fill_qty=order_request.qty,
                timestamp=candle.timestamp,
            )
            return [fill]
        else:
            # Simulate 2 partial fills: 50% and 50%
            half_qty = order_request.qty // 2
            remainder = order_request.qty - half_qty
            fill1 = Fill(
                client_order_id=order_request.client_order_id,
                broker_order_id=f"SIM_P1_{order_request.client_order_id}",
                symbol=order_request.symbol,
                side=order_request.side,
                fill_price=fill_price,
                fill_qty=half_qty,
                timestamp=candle.timestamp,
            )
            fill2 = Fill(
                client_order_id=order_request.client_order_id,
                broker_order_id=f"SIM_P2_{order_request.client_order_id}",
                symbol=order_request.symbol,
                side=order_request.side,
                fill_price=fill_price,
                fill_qty=remainder,
                timestamp=candle.timestamp,
            )
            return [fill1, fill2]
