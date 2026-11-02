"""Deterministic synthetic market data generator for backtest engine verification.

Produces 1-minute OHLCV candles for NIFTY spot and corresponding option contracts
under strictly controlled market scenarios (bullish breakout, bearish breakout,
atomic reversal, daily loss limit breach, profit protection lock, and missing data gaps).
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from src.common.constants import OptionType
from src.common.time_utils import IST_TZ, to_utc
from src.core.models import Candle


def _make_dt(session_date: date, time_str: str) -> datetime:
    """Helper to create timezone-aware IST datetime converted to UTC."""
    h, m, s = [int(x) for x in time_str.split(":")]
    naive = datetime(session_date.year, session_date.month, session_date.day, h, m, s)
    dt_ist = IST_TZ.localize(naive)
    return to_utc(dt_ist)


def generate_candle(
    symbol: str,
    timestamp: datetime,
    open_p: Decimal,
    high_p: Decimal,
    low_p: Decimal,
    close_p: Decimal,
    volume: int = 1000,
) -> Candle:
    """Create a validated Candle instance."""
    return Candle(
        symbol=symbol,
        timestamp=timestamp,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=volume,
    )


class SyntheticMarketDataBuilder:
    """
    Constructs deterministic, chronologically ordered 1-minute candle datasets
    for the NIFTY spot index and ATM option contracts.
    """

    def __init__(self, session_date: Optional[date] = None) -> None:
        # Default session date: Friday 2026-11-06
        self.session_date = session_date or date(2026, 11, 6)
        self.spot_symbol = "NSE:NIFTY50-INDEX"

    def build_bullish_breakout_dataset(
        self,
        ce_symbol: str = "NSE:NIFTY26NOV25000CE",
        base_spot: Decimal = Decimal("25000.00"),
        ce_entry_price: Decimal = Decimal("100.00"),
        ce_exit_price: Decimal = Decimal("130.00"),
    ) -> Tuple[List[Candle], Dict[str, List[Candle]]]:
        """
        Scenario 1: Bullish Breakout
        - OR (09:16 - 09:45): Range [24960, 25010]
        - 09:46: Spot closes at 25020 (> 25010 OR High) -> ATM 25000 Long CE signal
        - 09:47: CE fills at open (~100.00)
        - Day trends upward: CE reaches ~130.00
        - 15:10: Mandatory square-off at ~130.00
        """
        spot_candles: List[Candle] = []
        option_candles: Dict[str, List[Candle]] = {ce_symbol: []}

        # 09:15: pre-market / opening candle
        dt_0915 = _make_dt(self.session_date, "09:15:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_0915, base_spot, base_spot + 5, base_spot - 5, base_spot))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_0915, ce_entry_price - 5, ce_entry_price, ce_entry_price - 10, ce_entry_price - 5))

        # 09:16 to 09:45: Opening range [24960, 25010]
        for m in range(16, 46):
            t_str = f"09:{m:02d}:00"
            dt = _make_dt(self.session_date, t_str)
            h = base_spot + 10 if m == 20 else base_spot + 5
            l = base_spot - 40 if m == 35 else base_spot - 10
            c = base_spot
            spot_candles.append(generate_candle(self.spot_symbol, dt, c - 2, h, l, c))
            option_candles[ce_symbol].append(generate_candle(ce_symbol, dt, Decimal("95.00"), Decimal("102.00"), Decimal("94.00"), Decimal("98.00")))

        # 09:46: Breakout bar: Close = 25020 (> 25010 OR High) -> ATM is 25000
        dt_0946 = _make_dt(self.session_date, "09:46:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_0946, base_spot + 5, base_spot + 20, base_spot + 5, base_spot + 20))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_0946, Decimal("98.00"), Decimal("105.00"), Decimal("97.00"), Decimal("104.00")))

        # 09:47: Fill bar (Next-bar open fill occurs here: open is ce_entry_price)
        dt_0947 = _make_dt(self.session_date, "09:47:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_0947, base_spot + 20, base_spot + 35, base_spot + 15, base_spot + 30))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_0947, ce_entry_price, ce_entry_price + 5, ce_entry_price - 1, ce_entry_price + 4))

        # Intraday drift towards 15:10
        dt_1200 = _make_dt(self.session_date, "12:00:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_1200, base_spot + 40, base_spot + 60, base_spot + 35, base_spot + 55))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_1200, Decimal("115.00"), Decimal("122.00"), Decimal("114.00"), Decimal("120.00")))

        # 15:10: EOD square-off bar
        dt_1510 = _make_dt(self.session_date, "15:10:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_1510, base_spot + 60, base_spot + 75, base_spot + 55, base_spot + 70))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_1510, ce_exit_price, ce_exit_price + 3, ce_exit_price - 2, ce_exit_price))

        # 15:11: Post-exit fill bar
        dt_1511 = _make_dt(self.session_date, "15:11:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_1511, base_spot + 70, base_spot + 75, base_spot + 68, base_spot + 72))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_1511, ce_exit_price, ce_exit_price + 2, ce_exit_price - 2, ce_exit_price))

        return spot_candles, option_candles

    def build_bearish_breakout_dataset(
        self,
        pe_symbol: str = "NSE:NIFTY26NOV24900PE",
        base_spot: Decimal = Decimal("25000.00"),
        pe_entry_price: Decimal = Decimal("95.00"),
        pe_exit_price: Decimal = Decimal("125.00"),
    ) -> Tuple[List[Candle], Dict[str, List[Candle]]]:
        """
        Scenario 2: Bearish Breakout
        - OR (09:16 - 09:45): Range [24940, 24990]
        - 09:46: Spot closes at 24900 (< 24940 OR Low) -> ATM 24900 Long PE signal
        - 09:47: PE fills at open (~95.00)
        - Spot plunges into afternoon -> PE reaches ~125.00
        - 15:10: Mandatory square-off
        """
        spot_candles: List[Candle] = []
        option_candles: Dict[str, List[Candle]] = {pe_symbol: []}

        # 09:15
        dt_0915 = _make_dt(self.session_date, "09:15:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_0915, base_spot, base_spot + 5, base_spot - 5, base_spot))
        option_candles[pe_symbol].append(generate_candle(pe_symbol, dt_0915, Decimal("90.00"), Decimal("95.00"), Decimal("88.00"), Decimal("92.00")))

        # 09:16 to 09:45: Opening range [24940, 24990]
        for m in range(16, 46):
            t_str = f"09:{m:02d}:00"
            dt = _make_dt(self.session_date, t_str)
            h = base_spot - 10 if m == 20 else base_spot - 20
            l = base_spot - 60 if m == 35 else base_spot - 40
            c = base_spot - 30
            spot_candles.append(generate_candle(self.spot_symbol, dt, c + 2, h, l, c))
            option_candles[pe_symbol].append(generate_candle(pe_symbol, dt, Decimal("90.00"), Decimal("95.00"), Decimal("89.00"), Decimal("92.00")))

        # 09:46: Downside breakout bar: Close = 24900 (< 24940 OR Low) -> ATM is 24900
        dt_0946 = _make_dt(self.session_date, "09:46:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_0946, base_spot - 30, base_spot - 25, base_spot - 105, base_spot - 100))
        option_candles[pe_symbol].append(generate_candle(pe_symbol, dt_0946, Decimal("92.00"), Decimal("98.00"), Decimal("91.00"), Decimal("96.00")))

        # 09:47: Fill bar (Next-bar open fill occurs here: open is pe_entry_price)
        dt_0947 = _make_dt(self.session_date, "09:47:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_0947, base_spot - 100, base_spot - 95, base_spot - 120, base_spot - 115))
        option_candles[pe_symbol].append(generate_candle(pe_symbol, dt_0947, pe_entry_price, pe_entry_price + 4, pe_entry_price - 1, pe_entry_price + 3))

        # 15:10: EOD square-off bar
        dt_1510 = _make_dt(self.session_date, "15:10:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_1510, base_spot - 140, base_spot - 135, base_spot - 155, base_spot - 150))
        option_candles[pe_symbol].append(generate_candle(pe_symbol, dt_1510, pe_exit_price, pe_exit_price + 3, pe_exit_price - 2, pe_exit_price))

        # 15:11: Post-exit fill bar
        dt_1511 = _make_dt(self.session_date, "15:11:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_1511, base_spot - 150, base_spot - 145, base_spot - 155, base_spot - 152))
        option_candles[pe_symbol].append(generate_candle(pe_symbol, dt_1511, pe_exit_price, pe_exit_price + 2, pe_exit_price - 2, pe_exit_price))

        return spot_candles, option_candles

    def build_reversal_dataset(
        self,
        ce_symbol: str = "NSE:NIFTY26NOV25000CE",
        pe_symbol: str = "NSE:NIFTY26NOV24900PE",
        base_spot: Decimal = Decimal("25000.00"),
    ) -> Tuple[List[Candle], Dict[str, List[Candle]]]:
        """
        Scenario 3: Atomic Two-Phase Reversal
        - OR: [24950, 25010]
        - 09:46: Breakout above 25010 -> Long 25000 CE
        - 09:47: CE fills at 100.00
        - 10:15: Reversal candle: Spot crashes to 24900 (< 24950 OR Low) -> CLOSE_CE emitted
        - 10:16: CE exit fills at 90.00 (loss). Replacement ENTER_LONG_PE emitted (ATM 24900)!
        - 10:17: PE enters at 98.00
        - 15:10: Square-off PE at 125.00
        """
        spot_candles: List[Candle] = []
        option_candles: Dict[str, List[Candle]] = {ce_symbol: [], pe_symbol: []}

        # 09:15
        dt_0915 = _make_dt(self.session_date, "09:15:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_0915, base_spot, base_spot + 5, base_spot - 5, base_spot))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_0915, Decimal("95.00"), Decimal("98.00"), Decimal("94.00"), Decimal("96.00")))
        option_candles[pe_symbol].append(generate_candle(pe_symbol, dt_0915, Decimal("90.00"), Decimal("92.00"), Decimal("88.00"), Decimal("90.00")))

        # 09:16 to 09:45: Range [24950, 25010]
        for m in range(16, 46):
            t_str = f"09:{m:02d}:00"
            dt = _make_dt(self.session_date, t_str)
            h = base_spot + 10 if m == 20 else base_spot + 5
            l = base_spot - 50 if m == 35 else base_spot - 20
            c = base_spot
            spot_candles.append(generate_candle(self.spot_symbol, dt, c - 2, h, l, c))
            option_candles[ce_symbol].append(generate_candle(ce_symbol, dt, Decimal("98.00"), Decimal("101.00"), Decimal("97.00"), Decimal("99.00")))
            option_candles[pe_symbol].append(generate_candle(pe_symbol, dt, Decimal("88.00"), Decimal("90.00"), Decimal("86.00"), Decimal("87.00")))

        # 09:46: Upside breakout to 25020 (> 25010) -> ATM is 25000
        dt_0946 = _make_dt(self.session_date, "09:46:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_0946, base_spot + 5, base_spot + 25, base_spot + 5, base_spot + 20))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_0946, Decimal("99.00"), Decimal("103.00"), Decimal("98.00"), Decimal("102.00")))
        option_candles[pe_symbol].append(generate_candle(pe_symbol, dt_0946, Decimal("86.00"), Decimal("87.00"), Decimal("82.00"), Decimal("83.00")))

        # 09:47: CE Entry Fill bar at 100.00
        dt_0947 = _make_dt(self.session_date, "09:47:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_0947, base_spot + 20, base_spot + 35, base_spot + 15, base_spot + 30))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_0947, Decimal("100.00"), Decimal("104.00"), Decimal("99.00"), Decimal("103.00")))
        option_candles[pe_symbol].append(generate_candle(pe_symbol, dt_0947, Decimal("82.00"), Decimal("83.00"), Decimal("80.00"), Decimal("81.00")))

        # 10:15: Reversal Bar (Spot collapses to 24900 < 24950) -> ATM is 24900
        dt_1015 = _make_dt(self.session_date, "10:15:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_1015, base_spot + 15, base_spot + 20, base_spot - 105, base_spot - 100))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_1015, Decimal("98.00"), Decimal("99.00"), Decimal("88.00"), Decimal("89.00")))
        option_candles[pe_symbol].append(generate_candle(pe_symbol, dt_1015, Decimal("85.00"), Decimal("96.00"), Decimal("84.00"), Decimal("94.00")))

        # 10:16: CE Exit Fill Bar (CE exits at 90.00 open; PE entry queued)
        dt_1016 = _make_dt(self.session_date, "10:16:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_1016, base_spot - 100, base_spot - 95, base_spot - 110, base_spot - 105))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_1016, Decimal("90.00"), Decimal("91.00"), Decimal("85.00"), Decimal("86.00")))
        option_candles[pe_symbol].append(generate_candle(pe_symbol, dt_1016, Decimal("95.00"), Decimal("100.00"), Decimal("94.00"), Decimal("98.00")))

        # 10:17: PE Entry Fill Bar (PE enters at 98.00 open)
        dt_1017 = _make_dt(self.session_date, "10:17:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_1017, base_spot - 105, base_spot - 100, base_spot - 120, base_spot - 115))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_1017, Decimal("85.00"), Decimal("86.00"), Decimal("80.00"), Decimal("81.00")))
        option_candles[pe_symbol].append(generate_candle(pe_symbol, dt_1017, Decimal("98.00"), Decimal("105.00"), Decimal("97.00"), Decimal("103.00")))

        # 15:10: EOD Square-off Bar (PE exits at 125.00)
        dt_1510 = _make_dt(self.session_date, "15:10:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_1510, base_spot - 140, base_spot - 135, base_spot - 160, base_spot - 155))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_1510, Decimal("60.00"), Decimal("61.00"), Decimal("55.00"), Decimal("56.00")))
        option_candles[pe_symbol].append(generate_candle(pe_symbol, dt_1510, Decimal("125.00"), Decimal("128.00"), Decimal("124.00"), Decimal("126.00")))

        # 15:11: Post square-off
        dt_1511 = _make_dt(self.session_date, "15:11:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_1511, base_spot - 155, base_spot - 150, base_spot - 160, base_spot - 155))
        option_candles[pe_symbol].append(generate_candle(pe_symbol, dt_1511, Decimal("126.00"), Decimal("128.00"), Decimal("125.00"), Decimal("126.00")))

        return spot_candles, option_candles

    def build_daily_loss_limit_dataset(
        self,
        ce_symbol: str = "NSE:NIFTY26NOV25000CE",
        base_spot: Decimal = Decimal("25000.00"),
    ) -> Tuple[List[Candle], Dict[str, List[Candle]]]:
        """
        Scenario 4: Daily Loss Limit Circuit Breaker (-₹3,000 breach)
        - CE entered at 100.00 (qty 25: drop of > 120 points breaches -3000)
        - Option drops to 50.00 -> Loss limit breached!
        - Circuit breaker triggers: emergency exit order -> Lockout for day.
        """
        spot_candles: List[Candle] = []
        option_candles: Dict[str, List[Candle]] = {ce_symbol: []}

        # 09:15
        dt_0915 = _make_dt(self.session_date, "09:15:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_0915, base_spot, base_spot + 5, base_spot - 5, base_spot))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_0915, Decimal("95.00"), Decimal("98.00"), Decimal("94.00"), Decimal("96.00")))

        # 09:16 to 09:45: Range [24960, 25010]
        for m in range(16, 46):
            dt = _make_dt(self.session_date, f"09:{m:02d}:00")
            h = base_spot + 10 if m == 20 else base_spot + 5
            l = base_spot - 40 if m == 35 else base_spot - 20
            spot_candles.append(generate_candle(self.spot_symbol, dt, base_spot, h, l, base_spot))
            option_candles[ce_symbol].append(generate_candle(ce_symbol, dt, Decimal("98.00"), Decimal("101.00"), Decimal("97.00"), Decimal("99.00")))

        # 09:46: Upside breakout to 25020 (> 25010) -> ATM 25000
        dt_0946 = _make_dt(self.session_date, "09:46:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_0946, base_spot + 5, base_spot + 25, base_spot + 5, base_spot + 20))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_0946, Decimal("99.00"), Decimal("103.00"), Decimal("98.00"), Decimal("102.00")))

        # 09:47: CE Entry Fill bar at 100.00
        dt_0947 = _make_dt(self.session_date, "09:47:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_0947, base_spot + 20, base_spot + 35, base_spot + 15, base_spot + 30))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_0947, Decimal("100.00"), Decimal("104.00"), Decimal("99.00"), Decimal("103.00")))

        # 10:15: Severe adverse move -> Option plunges to 50.00 (loss = (50 - 100)*75 = -3750 < -3000)
        dt_1015 = _make_dt(self.session_date, "10:15:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_1015, base_spot - 50, base_spot - 40, base_spot - 200, base_spot - 190))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_1015, Decimal("65.00"), Decimal("65.00"), Decimal("48.00"), Decimal("50.00")))

        # 10:16: Fill emergency exit at 50.00
        dt_1016 = _make_dt(self.session_date, "10:16:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_1016, base_spot - 190, base_spot - 180, base_spot - 210, base_spot - 200))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_1016, Decimal("50.00"), Decimal("52.00"), Decimal("45.00"), Decimal("48.00")))

        # 11:00: Market moves again, but locked out -> No trades
        dt_1100 = _make_dt(self.session_date, "11:00:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_1100, base_spot + 150, base_spot + 180, base_spot + 140, base_spot + 175))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_1100, Decimal("150.00"), Decimal("160.00"), Decimal("145.00"), Decimal("155.00")))

        return spot_candles, option_candles

    def build_profit_protection_dataset(
        self,
        ce_symbol: str = "NSE:NIFTY26NOV25000CE",
        base_spot: Decimal = Decimal("25000.00"),
    ) -> Tuple[List[Candle], Dict[str, List[Candle]]]:
        """
        Scenario 5: Profit Protection Trigger (+₹3,000 peak -> +₹500 floor -> pullback exit)
        - CE entered at 100.00
        - Option peaks at 150.00 -> Profit protection triggers! Minimum lock floor = +₹500.
        - Option pulls back to 104.00 -> MTM falls below locked floor!
        - Profit protection exit triggers -> Square off with profit locked.
        """
        spot_candles: List[Candle] = []
        option_candles: Dict[str, List[Candle]] = {ce_symbol: []}

        # 09:15
        dt_0915 = _make_dt(self.session_date, "09:15:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_0915, base_spot, base_spot + 5, base_spot - 5, base_spot))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_0915, Decimal("95.00"), Decimal("98.00"), Decimal("94.00"), Decimal("96.00")))

        # 09:16 to 09:45: Range [24960, 25010]
        for m in range(16, 46):
            dt = _make_dt(self.session_date, f"09:{m:02d}:00")
            h = base_spot + 10 if m == 20 else base_spot + 5
            l = base_spot - 40 if m == 35 else base_spot - 20
            spot_candles.append(generate_candle(self.spot_symbol, dt, base_spot, h, l, base_spot))
            option_candles[ce_symbol].append(generate_candle(ce_symbol, dt, Decimal("98.00"), Decimal("101.00"), Decimal("97.00"), Decimal("99.00")))

        # 09:46: Upside breakout to 25020 (> 25010) -> ATM 25000
        dt_0946 = _make_dt(self.session_date, "09:46:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_0946, base_spot + 5, base_spot + 25, base_spot + 5, base_spot + 20))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_0946, Decimal("99.00"), Decimal("103.00"), Decimal("98.00"), Decimal("102.00")))

        # 09:47: CE Entry Fill bar at 100.00
        dt_0947 = _make_dt(self.session_date, "09:47:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_0947, base_spot + 20, base_spot + 35, base_spot + 15, base_spot + 30))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_0947, Decimal("100.00"), Decimal("104.00"), Decimal("99.00"), Decimal("103.00")))

        # 10:30: Option surges to 150.00 (MTM peaks: triggers profit protection)
        dt_1030 = _make_dt(self.session_date, "10:30:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_1030, base_spot + 150, base_spot + 250, base_spot + 145, base_spot + 240))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_1030, Decimal("130.00"), Decimal("152.00"), Decimal("128.00"), Decimal("150.00")))

        # 10:45: Deep pullback: Option drops to 104.00 (pullback breaches +₹500 floor)
        dt_1045 = _make_dt(self.session_date, "10:45:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_1045, base_spot + 120, base_spot + 125, base_spot + 60, base_spot + 65))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_1045, Decimal("115.00"), Decimal("116.00"), Decimal("103.00"), Decimal("104.00")))

        # 10:46: Exit fill bar
        dt_1046 = _make_dt(self.session_date, "10:46:00")
        spot_candles.append(generate_candle(self.spot_symbol, dt_1046, base_spot + 65, base_spot + 70, base_spot + 55, base_spot + 60))
        option_candles[ce_symbol].append(generate_candle(ce_symbol, dt_1046, Decimal("104.00"), Decimal("106.00"), Decimal("101.00"), Decimal("103.00")))

        return spot_candles, option_candles

    def build_missing_data_dataset(
        self,
        ce_symbol: str = "NSE:NIFTY26NOV25000CE",
        base_spot: Decimal = Decimal("25000.00"),
    ) -> Tuple[List[Candle], Dict[str, List[Candle]]]:
        """
        Scenario 6: Missing Option Data
        - Spot breaks out at 09:46.
        - Option candle is completely missing at 09:47 (fill bar).
        - Engine must detect the data gap and record diagnostic/halt cleanly.
        """
        spot_candles, option_candles = self.build_bullish_breakout_dataset(ce_symbol=ce_symbol, base_spot=base_spot)

        # Intentionally delete the fill bar (09:47) for option candles
        dt_0947 = _make_dt(self.session_date, "09:47:00")
        option_candles[ce_symbol] = [c for c in option_candles[ce_symbol] if c.timestamp != dt_0947]

        return spot_candles, option_candles
