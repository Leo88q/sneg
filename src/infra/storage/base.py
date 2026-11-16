"""Протокол файлового хранилища фотоотчётов (ТЗ B8.3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class StoredFile:
    storage_key: str
    size_bytes: int
    sha256: str
    mime_type: str


class FileStorage(Protocol):
    async def put_object(
        self, *, key: str, data: bytes, content_type: str = "image/jpeg"
    ) -> StoredFile: ...

    async def get_object(self, key: str) -> bytes: ...

    async def get_presigned_url(self, key: str, expires_sec: int = 900) -> str: ...

    async def delete_object(self, key: str) -> None: ...
