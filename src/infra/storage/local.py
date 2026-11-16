"""Локальное файловое хранилище для dev и sandbox."""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.core.exceptions import NotFoundError
from src.infra.storage.base import StoredFile


class LocalFileStorage:
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, key: str) -> Path:
        clean = key.lstrip("/").replace("..", "")
        target = (self.base_dir / clean).resolve()
        if not str(target).startswith(str(self.base_dir)):
            raise ValueError("Invalid storage key path traversal")
        return target

    async def put_object(
        self, *, key: str, data: bytes, content_type: str = "image/jpeg"
    ) -> StoredFile:
        path = self._safe_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        return StoredFile(
            storage_key=key,
            size_bytes=len(data),
            sha256=digest,
            mime_type=content_type,
        )

    async def get_object(self, key: str) -> bytes:
        path = self._safe_path(key)
        if not path.exists():
            raise NotFoundError(f"Файл не найден: {key}")
        return path.read_bytes()

    async def get_presigned_url(self, key: str, expires_sec: int = 900) -> str:
        # В локальном режиме раздача идёт через авторизованный прокси /api/v1/admin/media/{id}
        return f"/api/v1/media/raw/{key}"

    async def delete_object(self, key: str) -> None:
        path = self._safe_path(key)
        if path.exists():
            path.unlink()
