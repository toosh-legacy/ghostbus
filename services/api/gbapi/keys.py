"""Public API keys (local stand-in for API Management): hashed at rest, constant-time compare, per-key token bucket.

Read endpoints accept an optional `X-Api-Key`. Requests with a valid key get that key's quota (requests/minute);
anonymous requests share a per-client anonymous bucket. Every /v1 response carries RateLimit headers.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from gbcore.db import ApiKey, session_factory
from sqlalchemy import select

ANON_PER_MIN = 120


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_key(owner: str, quota_per_min: int = 600) -> str:
    raw = "gb_" + secrets.token_urlsafe(24)
    with session_factory().begin() as s:
        s.add(ApiKey(hash=hash_key(raw), owner_label=owner, quota=quota_per_min, created_at=datetime.now(UTC)))
    return raw


@dataclass
class Bucket:
    capacity: float
    tokens: float
    updated: float

    def take(self, now: float) -> bool:
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.capacity / 60.0)
        self.updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class KeyStore:
    def __init__(self) -> None:
        self._buckets: dict[str, Bucket] = {}
        self._cache: dict[str, int] = {}
        self._loaded = 0.0

    def _refresh(self) -> None:
        if time.time() - self._loaded < 30:
            return
        try:
            with session_factory()() as s:
                self._cache = {k.hash: k.quota for k in s.execute(select(ApiKey)).scalars()}
        except Exception:  # noqa: BLE001
            self._cache = {}
        self._loaded = time.time()

    def lookup(self, raw: str | None) -> tuple[str, int] | None:
        """(bucket id, quota) for a valid key; None for an invalid key."""
        if not raw:
            return None
        self._refresh()
        h = hash_key(raw)
        for stored, quota in self._cache.items():
            if hmac.compare_digest(stored, h):
                return f"key:{stored[:12]}", quota
        return None

    def check(self, bucket_id: str, quota: int) -> tuple[bool, int]:
        now = time.monotonic()
        b = self._buckets.setdefault(bucket_id, Bucket(float(quota), float(quota), now))
        ok = b.take(now)
        return ok, int(b.tokens)
