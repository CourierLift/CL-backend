"""Small object-storage abstraction for delivery proof files."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from ..settings import settings


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int
    content_type: str


class ObjectStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> StoredObject: ...
    def delete(self, key: str) -> None: ...


class LocalObjectStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("Invalid storage key")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return StoredObject(key=key, size_bytes=len(data), content_type=content_type)

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()


class S3ObjectStorage:
    def __init__(self, bucket: str, region: str, endpoint_url: str | None) -> None:
        import boto3

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
        )

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return StoredObject(key=key, size_bytes=len(data), content_type=content_type)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


@lru_cache(maxsize=1)
def get_object_storage() -> ObjectStorage:
    backend = settings.CL_OBJECT_STORAGE_BACKEND.strip().lower()
    if backend == "s3":
        if not settings.CL_S3_BUCKET:
            raise RuntimeError("CL_S3_BUCKET is required for S3 object storage")
        return S3ObjectStorage(
            bucket=settings.CL_S3_BUCKET,
            region=settings.CL_S3_REGION,
            endpoint_url=settings.CL_S3_ENDPOINT_URL,
        )
    return LocalObjectStorage(settings.CL_OBJECT_STORAGE_PATH)
