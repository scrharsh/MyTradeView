"""Unit tests for FYERS API v3 Market Data Adapter without live network calls."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock
import pytest

from src.common.exceptions import (
    BrokerConnectionError,
    BrokerError,
    DataValidationError,
)
from src.core.models import Candle, MarketTick
from src.data.fyers_adapter import FyersMarketDataAdapter


def test_parse_valid_fyers_history_response():
    """Verify parsing official FYERS /api/v3/history array format into Candle models."""
    adapter = FyersMarketDataAdapter()

    sample_fyers_response = {
        "s": "ok",
        "candles": [
            [1730519160, 24500.5, 24535.0, 24495.0, 24520.0, 15000],
            [1730519220, 24520.0, 24540.0, 24510.0, 24530.5, 12000],
        ],
    }

    candles = adapter.parse_history_response(sample_fyers_response, "NSE:NIFTY50-INDEX")

    assert len(candles) == 2
    assert isinstance(candles[0], Candle)
    assert candles[0].symbol == "NSE:NIFTY50-INDEX"
    assert candles[0].open == Decimal("24500.5")
    assert candles[0].high == Decimal("24535.0")
    assert candles[0].low == Decimal("24495.0")
    assert candles[0].close == Decimal("24520.0")
    assert candles[0].volume == 15000
    assert candles[0].timestamp.tzinfo == timezone.utc


def test_parse_fyers_history_empty_candles():
    """Verify empty candle response handling."""
    adapter = FyersMarketDataAdapter()
    response = {"s": "ok", "candles": []}
    candles = adapter.parse_history_response(response, "NSE:NIFTY50-INDEX")
    assert candles == []


def test_parse_fyers_history_rate_limit_error():
    """Verify rate limit (HTTP 429) triggers BrokerConnectionError."""
    adapter = FyersMarketDataAdapter()
    response = {
        "s": "error",
        "code": 429,
        "message": "Too many requests. Please slow down.",
    }

    with pytest.raises(BrokerConnectionError) as exc_info:
        adapter.parse_history_response(response, "NSE:NIFTY50-INDEX")
    assert "Rate Limit Exceeded" in str(exc_info.value)


def test_parse_fyers_history_token_expired_error():
    """Verify expired token (-15 / -16) triggers BrokerConnectionError."""
    adapter = FyersMarketDataAdapter()
    response = {
        "s": "error",
        "code": -15,
        "message": "Invalid token or token expired.",
    }

    with pytest.raises(BrokerConnectionError) as exc_info:
        adapter.parse_history_response(response, "NSE:NIFTY50-INDEX")
    assert "Authentication Token Expired" in str(exc_info.value)


def test_parse_valid_fyers_quote_response():
    """Verify parsing official FYERS /api/v3/quotes response into MarketTick."""
    adapter = FyersMarketDataAdapter()

    sample_quote_response = {
        "s": "ok",
        "d": [
            {
                "n": "NSE:NIFTY50-INDEX",
                "s": "ok",
                "v": {
                    "lp": 24525.75,
                    "volume": 2500000,
                    "cmd": {"t": 1730519160, "o": 24500.0, "h": 24550.0, "l": 24480.0, "c": 24525.75},
                    "bid": 24525.50,
                    "ask": 24526.00,
                },
            }
        ],
    }

    tick = adapter.parse_quote_response(sample_quote_response, "NSE:NIFTY50-INDEX")

    assert isinstance(tick, MarketTick)
    assert tick.symbol == "NSE:NIFTY50-INDEX"
    assert tick.ltp == Decimal("24525.75")
    assert tick.bid_price == Decimal("24525.50")
    assert tick.ask_price == Decimal("24526.00")
    assert tick.volume == 2500000


def test_offline_mode_without_network_calls():
    """Verify that unconfigured adapter fails closed without making any external network requests."""
    adapter = FyersMarketDataAdapter(client=None)

    # Calling async methods without client must raise BrokerConnectionError immediately
    with pytest.raises(BrokerConnectionError) as exc_info:
        import asyncio
        asyncio.run(
            adapter.get_historical_candles(
                symbol="NSE:NIFTY50-INDEX",
                resolution="1",
                start_time=datetime(2026, 11, 2, 9, 15, tzinfo=timezone.utc),
                end_time=datetime(2026, 11, 2, 15, 30, tzinfo=timezone.utc),
            )
        )
    assert "FYERS client is not configured" in str(exc_info.value)
