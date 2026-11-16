"""Token-bucket rate limiter под лимиты MAX API (≤25 RPS глобально, ≤2 req/s на чат)."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict


class InMemoryRateLimiter:
    def __init__(self, *, global_rps: int = 25, per_chat_rps: int = 2) -> None:
        self.global_interval = 1.0 / max(1, global_rps)
        self.per_chat_interval = 1.0 / max(1, per_chat_rps)
        self._global_lock = asyncio.Lock()
        self._global_next = 0.0
        self._chat_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._chat_next: dict[str, float] = defaultdict(float)

    async def acquire(self, chat_key: str | None = None) -> None:
        async with self._global_lock:
            now = time.monotonic()
            wait = self._global_next - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._global_next = time.monotonic() + self.global_interval

        if chat_key is not None:
            async with self._chat_locks[chat_key]:
                now = time.monotonic()
                wait = self._chat_next[chat_key] - now
                if wait > 0:
                    await asyncio.sleep(wait)
                self._chat_next[chat_key] = time.monotonic() + self.per_chat_interval
