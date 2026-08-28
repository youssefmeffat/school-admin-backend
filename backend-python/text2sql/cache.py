"""Lightweight in-process LRU cache for repeated Text-to-SQL questions.

The cache stores completed PipelineResult objects so repeated questions can
return without repeating:

    retrieval -> LLM SQL generation -> DB execution -> answer synthesis

This is intentionally dependency-free and remains suitable for a local/demo
deployment.

TTL is optional. For a database that can change while the application is
running, configure query_cache_ttl_seconds in PipelineConfig.
"""

import time
from collections import OrderedDict
from typing import Any, Optional, Tuple


class QueryCache:
    """Size-bounded LRU cache with optional TTL."""

    def __init__(
        self,
        max_size: int = 256,
        ttl_seconds: Optional[float] = None,
    ):
        if max_size < 1:
            raise ValueError("max_size must be at least 1")

        if ttl_seconds is not None and ttl_seconds < 0:
            raise ValueError("ttl_seconds cannot be negative")

        self.max_size = max_size
        self.ttl_seconds = ttl_seconds

        self._store: "OrderedDict[Tuple, Tuple[float, Any]]" = OrderedDict()

        self.hits = 0
        self.misses = 0

    @staticmethod
    def _normalize_text(value: Optional[str]) -> str:
        """Normalize user text for stable cache keys."""

        if not value:
            return ""

        return " ".join(value.strip().lower().split())

    @classmethod
    def make_key(
        cls,
        question: str,
        conversation_context: Optional[str],
        *extra,
    ) -> Tuple:
        """Build a stable cache key.

        The extra values are intentionally preserved because callers can
        include configuration/schema/retrieval settings that affect the
        generated answer.
        """

        normalized_question = cls._normalize_text(question)
        normalized_context = cls._normalize_text(conversation_context)

        return (
            normalized_question,
            normalized_context,
            extra,
        )

    def _is_expired(self, timestamp: float) -> bool:
        """Return True when an entry exceeded its configured TTL."""

        if self.ttl_seconds is None:
            return False

        return (
            time.monotonic() - timestamp
        ) > self.ttl_seconds

    def get(self, key: Tuple) -> Optional[Any]:
        """Return a cached value or None when it is not available."""

        entry = self._store.get(key)

        if entry is None:
            self.misses += 1
            return None

        timestamp, value = entry

        if self._is_expired(timestamp):
            del self._store[key]
            self.misses += 1
            return None

        # Move recently-used entries to the end of the OrderedDict.
        self._store.move_to_end(key)

        self.hits += 1

        return value

    def set(self, key: Tuple, value: Any) -> None:
        """Store or replace a cached value."""

        # If the key already exists, replacing it should also refresh its
        # LRU position and timestamp.
        self._store[key] = (
            time.monotonic(),
            value,
        )

        self._store.move_to_end(key)

        # Remove expired entries while we are already touching the cache.
        if self.ttl_seconds is not None:
            expired_keys = [
                cache_key
                for cache_key, (timestamp, _) in self._store.items()
                if self._is_expired(timestamp)
            ]

            for cache_key in expired_keys:
                self._store.pop(cache_key, None)

        # Enforce the maximum cache size.
        while len(self._store) > self.max_size:
            self._store.popitem(last=False)

    def delete(self, key: Tuple) -> bool:
        """Delete one cached entry.

        Returns True when an entry existed and was removed.
        """

        return self._store.pop(key, None) is not None

    def clear(self) -> None:
        """Clear all cached results and reset cache statistics."""

        self._store.clear()

        self.hits = 0
        self.misses = 0

    @property
    def stats(self) -> dict:
        """Return cache statistics."""

        total = self.hits + self.misses

        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": (
                self.hits / total
                if total
                else 0.0
            ),
            "size": len(self._store),
            "max_size": self.max_size,
            "ttl_seconds": self.ttl_seconds,
        }