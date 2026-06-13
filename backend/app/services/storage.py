"""Storage abstraction layer with local and S3/MinIO backends.

Select backend via PAPERFORGE_STORAGE_BACKEND env var (default: "local").
S3 backend config: S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET.
"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path

from app.database import backend_dir

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = backend_dir / "data"
_LOCAL_DATA_DIR = Path(os.getenv("PAPERFORGE_DATA_DIR", str(_DEFAULT_DATA_DIR)))


def _sanitize_name(filename: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", filename or "file")
    if "." not in safe[-8:]:
        safe += ".bin"
    return safe


# ── Abstract backend ─────────────────────────────────────────────


class StorageBackend(ABC):
    @abstractmethod
    def save_pdf(self, project_id: str, paper_id: str, filename: str, content: bytes) -> str:
        """Save a PDF file. Returns a path/URI string."""

    @abstractmethod
    def save_tei(self, project_id: str, paper_id: str, text: str) -> str:
        """Save a TEI XML file. Returns a path/URI string."""

    @abstractmethod
    def save_export(self, project_id: str, filename: str, content: bytes) -> str:
        """Save an exported file. Returns a path/URI string."""

    @abstractmethod
    def read(self, path: str) -> bytes:
        """Read file content from a path/URI previously returned by save_*."""

    @abstractmethod
    def ensure_export_dir(self, project_id: str) -> str:
        """Ensure the export directory exists. Returns its path/URI prefix."""


# ── Local filesystem backend ─────────────────────────────────────


class LocalStorage(StorageBackend):
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or _LOCAL_DATA_DIR

    # -- pdf --
    def save_pdf(self, project_id: str, paper_id: str, filename: str, content: bytes) -> str:
        target_dir = self.base_dir / "storage" / project_id / "pdf"
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _sanitize_name(filename)
        if not safe_name.lower().endswith(".pdf"):
            safe_name += ".pdf"
        target = target_dir / f"{paper_id}_{safe_name}"
        target.write_bytes(content)
        return str(target)

    # -- tei --
    def save_tei(self, project_id: str, paper_id: str, text: str) -> str:
        target_dir = self.base_dir / "storage" / project_id / "tei"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{paper_id}.tei.xml"
        escaped = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )
        target.write_text(
            "<TEI><text><body>"
            + "".join(f"<p>{escaped}</p>" for line in text.splitlines() if line.strip())
            + "</body></text></TEI>",
            encoding="utf-8",
        )
        return str(target)

    # -- exports --
    def save_export(self, project_id: str, filename: str, content: bytes) -> str:
        target_dir = self.base_dir / "exports" / project_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / _sanitize_name(filename)
        target.write_bytes(content)
        return str(target)

    def read(self, path: str) -> bytes:
        return Path(path).read_bytes()

    def ensure_export_dir(self, project_id: str) -> str:
        target_dir = self.base_dir / "exports" / project_id
        target_dir.mkdir(parents=True, exist_ok=True)
        return str(target_dir)


# ── S3 / MinIO backend ───────────────────────────────────────────


class S3Storage(StorageBackend):
    def __init__(
        self,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
    ) -> None:
        self._endpoint = endpoint or os.getenv("S3_ENDPOINT", "localhost:9000")
        self._access_key = access_key or os.getenv("S3_ACCESS_KEY", "minio")
        self._secret_key = secret_key or os.getenv("S3_SECRET_KEY", "minio123456")
        self._bucket = bucket or os.getenv("S3_BUCKET", "paperforge")
        self._client: object | None = None

    @property
    def client(self) -> object:
        if self._client is None:
            try:
                from minio import Minio  # type: ignore

                secure = self._endpoint.startswith("https://")
                endpoint = self._endpoint.replace("https://", "").replace("http://", "")
                self._client = Minio(
                    endpoint,
                    access_key=self._access_key,
                    secret_key=self._secret_key,
                    secure=secure,
                )
                if not self._client.bucket_exists(self._bucket):
                    self._client.make_bucket(self._bucket)
                    logger.info("Created MinIO bucket '%s'", self._bucket)
            except ImportError:
                raise RuntimeError(
                    "minio package not installed. Run: pip install minio"
                ) from None
            except Exception as exc:
                logger.error("Failed to connect to MinIO at %s: %s", self._endpoint, exc)
                raise
        return self._client

    def _put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        from io import BytesIO
        self.client.put_object(  # type: ignore[union-attr]
            self._bucket, key, BytesIO(data), len(data), content_type=content_type,
        )
        return f"s3://{self._bucket}/{key}"

    def save_pdf(self, project_id: str, paper_id: str, filename: str, content: bytes) -> str:
        safe = _sanitize_name(filename)
        if not safe.lower().endswith(".pdf"):
            safe += ".pdf"
        key = f"storage/{project_id}/pdf/{paper_id}_{safe}"
        return self._put(key, content, "application/pdf")

    def save_tei(self, project_id: str, paper_id: str, text: str) -> str:
        key = f"storage/{project_id}/tei/{paper_id}.tei.xml"
        return self._put(key, text.encode("utf-8"), "application/xml")

    def save_export(self, project_id: str, filename: str, content: bytes) -> str:
        key = f"exports/{project_id}/{_sanitize_name(filename)}"
        return self._put(key, content)

    def read(self, path: str) -> bytes:
        if path.startswith("s3://"):
            _, _, obj_key = path.partition(f"{self._bucket}/")
        else:
            obj_key = path
        try:
            response = self.client.get_object(self._bucket, obj_key)  # type: ignore[union-attr]
            return response.read()
        finally:
            if "response" in locals():
                response.close()
                response.release_conn()

    def ensure_export_dir(self, project_id: str) -> str:
        return f"s3://{self._bucket}/exports/{project_id}"


# ── Backend selection ────────────────────────────────────────────

_backend: StorageBackend | None = None


def get_storage_backend() -> StorageBackend:
    global _backend
    if _backend is None:
        choice = os.getenv("PAPERFORGE_STORAGE_BACKEND", "local").strip().lower()
        if choice == "s3":
            _backend = S3Storage()
        else:
            _backend = LocalStorage()
        logger.info("Storage backend: %s", type(_backend).__name__)
    return _backend
