"""
Shared in-memory cache helpers for backend market data and analysis.

This keeps the implementation lightweight while giving us:
- TTL-based expiry
- bounded size
- targeted invalidation
- optional in-flight request coalescing
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import threading
import time
from typing import Any, Callable, Hashable


@dataclass
class CacheEntry:
    value: Any
    created_at: float


class TTLCacheStore:
    def __init__(self, maxsize: int = 256):
        self.maxsize = maxsize
        self._store: OrderedDict[Hashable, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._inflight: dict[Hashable, threading.Lock] = {}

    def _purge_expired(self, ttl_seconds: int | None = None) -> None:
        if ttl_seconds is None:
            return
        now = time.time()
        stale_keys = [k for k, entry in self._store.items() if now - entry.created_at >= ttl_seconds]
        for key in stale_keys:
            self._store.pop(key, None)

    def get(self, key: Hashable, ttl_seconds: int | None = None):
        with self._lock:
            self._purge_expired(ttl_seconds)
            entry = self._store.get(key)
            if not entry:
                return None
            self._store.move_to_end(key)
            return entry.value

    def get_with_age(self, key: Hashable, ttl_seconds: int | None = None):
        with self._lock:
            self._purge_expired(ttl_seconds)
            entry = self._store.get(key)
            if not entry:
                return None, None
            self._store.move_to_end(key)
            return entry.value, time.time() - entry.created_at

    def set(self, key: Hashable, value: Any) -> None:
        with self._lock:
            self._store[key] = CacheEntry(value=value, created_at=time.time())
            self._store.move_to_end(key)
            while len(self._store) > self.maxsize:
                self._store.popitem(last=False)

    def delete(self, key: Hashable) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def delete_matching(self, predicate: Callable[[Hashable], bool]) -> int:
        with self._lock:
            keys = [k for k in self._store if predicate(k)]
            for key in keys:
                self._store.pop(key, None)
            return len(keys)

    def with_inflight_lock(self, key: Hashable):
        with self._lock:
            lock = self._inflight.get(key)
            if lock is None:
                lock = threading.Lock()
                self._inflight[key] = lock
            return lock


def stable_cache_key(*parts: Any) -> tuple:
    return tuple(parts)
