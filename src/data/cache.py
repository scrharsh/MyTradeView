"""Reproducible market data and instrument metadata caching layer."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from src.common.time_utils import now_utc, to_utc


class CacheEntryMetadata(BaseModel):
    """Metadata tracked for every cached response and instrument record."""
    model_config = ConfigDict(frozen=True)

    cache_key: str
    data_source: str
    retrieval_timestamp: datetime = Field(default_factory=now_utc)
    contract_identifier: str
    expiry_date: Optional[str] = None
    validation_status: str = "VALID"
    record_count: int = 0
    coverage_start: Optional[str] = None
    coverage_end: Optional[str] = None
    known_gaps: List[Dict[str, str]] = Field(default_factory=list)
    ttl_seconds: int = 86400  # Default 24 hours


class MarketDataCache:
    """
    Disk and memory cache for historical candle responses and instrument metadata.
    Enforces cache invalidation so expired contracts or stale data are never silently served.
    """

    def __init__(self, cache_dir: Path = Path("./data/cache")) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory_cache: Dict[str, Any] = {}
        self._metadata_index: Dict[str, CacheEntryMetadata] = {}
        self._load_index()

    def _index_path(self) -> Path:
        return self._cache_dir / "cache_index.json"

    def _load_index(self) -> None:
        """Load metadata index from disk."""
        if self._index_path().exists():
            try:
                with open(self._index_path(), "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.items():
                    self._metadata_index[k] = CacheEntryMetadata(**v)
            except Exception:
                self._metadata_index = {}

    def _save_index(self) -> None:
        """Persist metadata index to disk."""
        data = {k: v.model_dump(mode="json") for k, v in self._metadata_index.items()}
        with open(self._index_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get(self, cache_key: str) -> Optional[Any]:
        """
        Retrieve data from cache if not expired.
        Returns None if cache is missing or expired.
        """
        metadata = self._metadata_index.get(cache_key)
        if not metadata:
            return None

        # Check TTL
        age_seconds = (now_utc() - metadata.retrieval_timestamp).total_seconds()
        if age_seconds > metadata.ttl_seconds:
            self.invalidate(cache_key)
            return None

        # Check in memory
        if cache_key in self._memory_cache:
            return self._memory_cache[cache_key]

        # Check on disk
        entry_file = self._cache_dir / f"{cache_key}.json"
        if entry_file.exists():
            try:
                with open(entry_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._memory_cache[cache_key] = data
                return data
            except Exception:
                return None

        return None

    def put(
        self,
        cache_key: str,
        data: Any,
        source: str,
        contract_identifier: str,
        expiry_date: Optional[str] = None,
        record_count: int = 0,
        coverage_start: Optional[str] = None,
        coverage_end: Optional[str] = None,
        known_gaps: Optional[List[Dict[str, str]]] = None,
        ttl_seconds: int = 86400,
    ) -> CacheEntryMetadata:
        """Store item in cache with complete provenance metadata."""
        metadata = CacheEntryMetadata(
            cache_key=cache_key,
            data_source=source,
            retrieval_timestamp=now_utc(),
            contract_identifier=contract_identifier,
            expiry_date=expiry_date,
            validation_status="VALID",
            record_count=record_count,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            known_gaps=known_gaps or [],
            ttl_seconds=ttl_seconds,
        )

        self._memory_cache[cache_key] = data
        self._metadata_index[cache_key] = metadata

        # Persist to disk
        entry_file = self._cache_dir / f"{cache_key}.json"
        with open(entry_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        self._save_index()
        return metadata

    def invalidate(self, cache_key: str) -> None:
        """Evict entry from memory and disk."""
        self._memory_cache.pop(cache_key, None)
        self._metadata_index.pop(cache_key, None)
        entry_file = self._cache_dir / f"{cache_key}.json"
        if entry_file.exists():
            entry_file.unlink()
        self._save_index()

    def clear(self) -> None:
        """Clear all cache data."""
        self._memory_cache.clear()
        self._metadata_index.clear()
        for f in self._cache_dir.glob("*.json"):
            f.unlink()
        self._save_index()

    def get_metadata(self, cache_key: str) -> Optional[CacheEntryMetadata]:
        """Fetch metadata for cache entry."""
        return self._metadata_index.get(cache_key)
