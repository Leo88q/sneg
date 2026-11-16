"""Хранилище состояний диалогов FSM (Redis в prod, In-Memory в dev/tests)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class FSMRecord:
    state: str
    data: dict[str, Any] = field(default_factory=dict)


class FSMStorage(Protocol):
    async def get(self, max_user_id: int) -> FSMRecord | None: ...

    async def set(
        self, max_user_id: int, state: str, data: dict[str, Any] | None = None
    ) -> None: ...

    async def update_data(self, max_user_id: int, **kwargs: Any) -> dict[str, Any]: ...

    async def clear(self, max_user_id: int) -> None: ...


class InMemoryFSMStorage:
    def __init__(self, ttl_seconds: int = 21600) -> None:
        self.ttl_seconds = ttl_seconds
        self._store: dict[int, tuple[float, FSMRecord]] = {}

    async def get(self, max_user_id: int) -> FSMRecord | None:
        item = self._store.get(max_user_id)
        if item is None:
            return None
        expires_at, rec = item
        if time.monotonic() > expires_at:
            self._store.pop(max_user_id, None)
            return None
        return FSMRecord(state=rec.state, data=dict(rec.data))

    async def set(
        self, max_user_id: int, state: str, data: dict[str, Any] | None = None
    ) -> None:
        prev = await self.get(max_user_id)
        merged = dict(prev.data) if (prev and data is None) else dict(data or {})
        self._store[max_user_id] = (
            time.monotonic() + self.ttl_seconds,
            FSMRecord(state=state, data=merged),
        )

    async def update_data(self, max_user_id: int, **kwargs: Any) -> dict[str, Any]:
        prev = await self.get(max_user_id)
        state = prev.state if prev else "idle"
        merged = dict(prev.data) if prev else {}
        merged.update(kwargs)
        self._store[max_user_id] = (
            time.monotonic() + self.ttl_seconds,
            FSMRecord(state=state, data=merged),
        )
        return merged

    async def clear(self, max_user_id: int) -> None:
        self._store.pop(max_user_id, None)


class RedisFSMStorage:
    def __init__(self, redis_client: Any, ttl_seconds: int = 21600) -> None:
        self._redis = redis_client
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _key(max_user_id: int) -> str:
        return f"fsm:{max_user_id}"

    async def get(self, max_user_id: int) -> FSMRecord | None:
        raw = await self._redis.get(self._key(max_user_id))
        if not raw:
            return None
        parsed = json.loads(raw)
        return FSMRecord(state=str(parsed["state"]), data=dict(parsed.get("data", {})))

    async def set(
        self, max_user_id: int, state: str, data: dict[str, Any] | None = None
    ) -> None:
        prev = await self.get(max_user_id)
        merged = dict(prev.data) if (prev and data is None) else dict(data or {})
        payload = json.dumps({"state": state, "data": merged}, ensure_ascii=False)
        await self._redis.set(self._key(max_user_id), payload, ex=self.ttl_seconds)

    async def update_data(self, max_user_id: int, **kwargs: Any) -> dict[str, Any]:
        prev = await self.get(max_user_id)
        state = prev.state if prev else "idle"
        merged = dict(prev.data) if prev else {}
        merged.update(kwargs)
        await self.set(max_user_id, state, merged)
        return merged

    async def clear(self, max_user_id: int) -> None:
        await self._redis.delete(self._key(max_user_id))
