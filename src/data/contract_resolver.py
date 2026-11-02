"""Authoritative Option Contract and Expiry Resolver for NIFTY derivatives."""

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Set

from src.common.constants import (
    ExpiryDaySelectionMode,
    OptionType,
)
from src.common.exceptions import DataValidationError
from src.core.models import OptionContract
from src.data.instrument_master import InstrumentMasterRegistry


class ContractResolver:
    """
    Resolves exact ATM strike, weekly expiry, dynamic lot size, and official
    FYERS trading symbols using authoritative instrument master records.
    Fails closed if contracts cannot be resolved unambiguously.
    """

    def __init__(
        self,
        registry: InstrumentMasterRegistry,
        strike_interval: Decimal = Decimal("50.00"),
        exchange_holidays: Optional[Set[date]] = None,
    ) -> None:
        self._registry = registry
        self._strike_interval = strike_interval
        self._exchange_holidays: Set[date] = exchange_holidays or set()

    # --------------------------------------------------------------------------
    # 1. ATM Strike Calculation
    # --------------------------------------------------------------------------

    def calculate_atm_strike(self, spot_price: Decimal) -> Decimal:
        """
        Calculate ATM strike rounded to nearest strike interval (50 for NIFTY):
        Strike_ATM = round(Spot / 50) * 50 using arithmetic ROUND_HALF_UP.
        """
        if spot_price <= 0:
            raise DataValidationError(f"Invalid non-positive spot price for ATM resolution: {spot_price}")

        quotient = (spot_price / self._strike_interval).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return quotient * self._strike_interval

    # --------------------------------------------------------------------------
    # 2. Expiry Date Resolution (Accounting for Holidays & Expiry-Day Choice)
    # --------------------------------------------------------------------------

    def resolve_weekly_expiry(
        self,
        underlying: str,
        session_date: date,
        expiry_day_mode: ExpiryDaySelectionMode = ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE,
    ) -> date:
        """
        Resolve the active weekly option expiry date from authoritative instrument records.
        Accounts for holiday adjustments and configurable expiry day behavior.
        Fails closed if no eligible expiry exists.
        """
        available_expiries = self._registry.get_expiries(underlying)
        if not available_expiries:
            raise DataValidationError(
                f"No option expiration dates found in instrument master for underlying '{underlying}'. "
                "Ensure instrument master is loaded."
            )

        # Filter expiries on or after current session date
        future_or_today_expiries = [exp for exp in available_expiries if exp >= session_date]
        if not future_or_today_expiries:
            raise DataValidationError(
                f"No active or future expiries available for {underlying} on session date {session_date}. "
                f"Max listed expiry is {available_expiries[-1]}."
            )

        nearest_expiry = future_or_today_expiries[0]

        # Expiry day choice: When today is expiry day (session_date == nearest_expiry)
        if nearest_expiry == session_date:
            if expiry_day_mode == ExpiryDaySelectionMode.NEXT_WEEKLY:
                if len(future_or_today_expiries) < 2:
                    raise DataValidationError(
                        f"Cannot select NEXT_WEEKLY expiry on {session_date}: "
                        "No subsequent weekly contract available in master."
                    )
                return future_or_today_expiries[1]
            return nearest_expiry

        return nearest_expiry

    # --------------------------------------------------------------------------
    # 3. Contract Resolution & Verification Against Instrument Master
    # --------------------------------------------------------------------------

    def resolve_atm_contract(
        self,
        underlying: str,
        spot_price: Decimal,
        option_type: OptionType,
        session_date: date,
        expiry_day_mode: ExpiryDaySelectionMode = ExpiryDaySelectionMode.CURRENT_WEEKLY_0DTE,
    ) -> OptionContract:
        """
        Resolve authoritative ATM option contract matching underlying spot price and session date.
        Never guesses symbols or hardcodes lot sizes. Fails closed if not found.
        """
        if not self._registry.is_loaded:
            raise DataValidationError("Cannot resolve contract: Instrument master registry is empty.")

        # 1. Resolve ATM strike
        atm_strike = self.calculate_atm_strike(spot_price)

        # 2. Resolve eligible weekly expiry
        resolved_expiry = self.resolve_weekly_expiry(
            underlying=underlying,
            session_date=session_date,
            expiry_day_mode=expiry_day_mode,
        )

        # 3. Query authoritative registry
        contract = self._registry.get_contract(
            underlying=underlying,
            expiry=resolved_expiry,
            strike=atm_strike,
            option_type=option_type,
        )

        if contract is None:
            # Check available strikes for diagnostics
            available_strikes = self._registry.get_strikes(underlying, resolved_expiry)
            raise DataValidationError(
                f"FAILED TO RESOLVE CONTRACT: No listed {option_type.value} contract found in master for "
                f"Underlying={underlying}, Expiry={resolved_expiry}, Strike={atm_strike}. "
                f"Available strikes for this expiry: {available_strikes[:5]} ... {available_strikes[-5:] if len(available_strikes) > 5 else ''}"
            )

        # Validate that dynamic lot size is present
        if contract.lot_size <= 0:
            raise DataValidationError(
                f"Contract '{contract.symbol}' has invalid lot size: {contract.lot_size}."
            )

        return contract
