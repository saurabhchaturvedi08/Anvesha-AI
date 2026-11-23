"""Storage for original files (uploaded and downloaded PDFs).

Self-hosted installs keep files on a local volume. The interface is small on purpose, so
an S3-compatible backend (Cloudflare R2, MinIO) can be added for the hosted service.
"""

import asyncio
import os
import re
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import Protocol

_KEY_PART = re.compile(r"^[A-Za-z0-9._-]+$")


class FileStorage(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def delete_prefix(self, prefix: str) -> None: ...


def source_file_key(
    workspace_id: uuid.UUID, project_id: uuid.UUID, source_id: uuid.UUID, extension: str
) -> str:
    extension = extension.lower().lstrip(".")
    if not _KEY_PART.match(extension):
        extension = "bin"
    return f"{project_prefix(workspace_id, project_id)}/sources/{source_id}/original.{extension}"


def project_prefix(workspace_id: uuid.UUID, project_id: uuid.UUID) -> str:
    return f"workspaces/{workspace_id}/projects/{project_id}"


class LocalFileStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _path(self, key: str) -> Path:
        parts = PurePosixPath(key).parts
        if not parts or any(not _KEY_PART.match(part) or part in (".", "..") for part in parts):
            raise ValueError(f"Invalid storage key {key!r}")
        path = self._root.joinpath(*parts)
        if not path.resolve().is_relative_to(self._root):
            raise ValueError(f"Invalid storage key {key!r}")
        return path

    async def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        await asyncio.to_thread(self._write, path, data)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)  # atomic: readers never see a half-written file

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)

    async def delete_prefix(self, prefix: str) -> None:
        path = self._path(prefix)
        await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
