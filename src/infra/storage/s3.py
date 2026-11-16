"""S3-совместимое хранилище (Yandex Object Storage / Selectel / MinIO в РФ)."""

from __future__ import annotations

import asyncio
import hashlib

import boto3
from botocore.client import Config

from src.infra.storage.base import StoredFile


class S3FileStorage:
    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
    ) -> None:
        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
        )

    async def put_object(
        self, *, key: str, data: bytes, content_type: str = "image/jpeg"
    ) -> StoredFile:
        def _upload() -> None:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )

        await asyncio.to_thread(_upload)
        return StoredFile(
            storage_key=key,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            mime_type=content_type,
        )

    async def get_object(self, key: str) -> bytes:
        def _download() -> bytes:
            resp = self._client.get_object(Bucket=self.bucket, Key=key)
            return bytes(resp["Body"].read())

        return await asyncio.to_thread(_download)

    async def get_presigned_url(self, key: str, expires_sec: int = 900) -> str:
        def _presign() -> str:
            return str(
                self._client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket, "Key": key},
                    ExpiresIn=expires_sec,
                )
            )

        return await asyncio.to_thread(_presign)

    async def delete_object(self, key: str) -> None:
        await asyncio.to_thread(
            lambda: self._client.delete_object(Bucket=self.bucket, Key=key)
        )
