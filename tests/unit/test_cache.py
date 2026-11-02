"""Unit tests for Market Data Cache and provenance metadata."""

from datetime import datetime, timezone
from pathlib import Path
import pytest

from src.data.cache import MarketDataCache


@pytest.fixture
def temp_cache_dir(tmp_path: Path) -> Path:
    cache_dir = tmp_path / "test_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def test_cache_put_get_and_provenance(temp_cache_dir: Path):
    """Verify storing, retrieving, and metadata tracking in cache."""
    cache = MarketDataCache(cache_dir=temp_cache_dir)

    sample_candles = [
        {"timestamp": "2026-11-02T03:46:00Z", "open": "24500.0", "close": "24510.0"},
    ]

    meta = cache.put(
        cache_key="test_nifty_candles",
        data=sample_candles,
        source="TestCSVSource",
        contract_identifier="NSE:NIFTY50-INDEX",
        record_count=1,
        coverage_start="2026-11-02T03:46:00Z",
        coverage_end="2026-11-02T03:47:00Z",
    )

    assert meta.cache_key == "test_nifty_candles"
    assert meta.record_count == 1
    assert meta.data_source == "TestCSVSource"

    # Retrieve from cache
    fetched = cache.get("test_nifty_candles")
    assert fetched == sample_candles

    # Query metadata
    meta_fetched = cache.get_metadata("test_nifty_candles")
    assert meta_fetched is not None
    assert meta_fetched.contract_identifier == "NSE:NIFTY50-INDEX"


def test_cache_invalidation_and_clear(temp_cache_dir: Path):
    """Verify cache invalidation removes entries from memory and disk."""
    cache = MarketDataCache(cache_dir=temp_cache_dir)
    cache.put(
        cache_key="key1",
        data={"value": 123},
        source="test",
        contract_identifier="ID1",
    )

    assert cache.get("key1") == {"value": 123}

    # Invalidate key1
    cache.invalidate("key1")
    assert cache.get("key1") is None
    assert cache.get_metadata("key1") is None


def test_cache_ttl_expiration(temp_cache_dir: Path):
    """Verify that entries exceeding TTL are expired and evicted."""
    cache = MarketDataCache(cache_dir=temp_cache_dir)
    # Set TTL = 0 seconds (instantly expired)
    cache.put(
        cache_key="expired_key",
        data={"val": 456},
        source="test",
        contract_identifier="ID_EXP",
        ttl_seconds=-1,
    )

    assert cache.get("expired_key") is None
