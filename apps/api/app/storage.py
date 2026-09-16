"""Object storage abstraction (BUILD_FLOW Phase 2).

Raw PDFs and extracted text are preserved in object storage (MinIO/S3 in
compose); local filesystem backend for dev and tests.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class StorageBackend(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Store bytes; return the key used."""
        ...

    def get_bytes(self, key: str) -> bytes:
        ...

    def delete(self, key: str) -> None:
        ...


def raw_pdf_key(content_hash: str) -> str:
    return f"raw/{content_hash}.pdf"


def raw_text_key(document_id: str) -> str:
    return f"text/{document_id}.txt"


class LocalStorageBackend:
    def __init__(self, base_dir: str | Path = "./var/storage"):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = self.base / key
        if Path(os.path.abspath(p)).as_posix().find(Path(os.path.abspath(self.base)).as_posix()) != 0:
            raise ValueError(f"unsafe storage key: {key!r}")
        return p

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass


class S3StorageBackend:
    def __init__(self, endpoint_url: str, bucket: str, access_key: str, secret_key: str, region: str = "us-east-1"):
        try:
            import boto3
        except ImportError as e:
            raise RuntimeError("boto3 is required for S3 storage") from e
        self.bucket = bucket
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        try:
            self.s3.head_bucket(Bucket=bucket)
        except Exception:
            self.s3.create_bucket(Bucket=bucket)

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def get_bytes(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)


def build_storage_from_env() -> StorageBackend:
    if os.getenv("USE_S3", "").lower() in ("1", "true", "yes"):
        return S3StorageBackend(
            endpoint_url=os.getenv("S3_ENDPOINT_URL", "http://minio:9000"),
            bucket=os.getenv("S3_BUCKET", "study-raw"),
            access_key=os.getenv("S3_ACCESS_KEY", "minioadmin"),
            secret_key=os.getenv("S3_SECRET_KEY", "minioadmin"),
        )
    return LocalStorageBackend(os.getenv("LOCAL_STORAGE_DIR", "./var/storage"))
