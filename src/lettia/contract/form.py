"""Multipart and urlencoded form support, and the UploadFile container."""

from collections.abc import AsyncIterator
from tempfile import SpooledTemporaryFile
from typing import BinaryIO

from lettia.core import Headers

_DEFAULT_SPOOL_MAX_SIZE = 1024 * 1024  # 1 MiB in memory before spilling to disk


class UploadFile:
    """An uploaded file buffered in memory or on disk.

    Small files (up to 1 MiB) stay in memory; larger files spill to a temporary
    file on disk.
    """

    __slots__ = ("_file", "content_type", "filename", "headers", "size")

    def __init__(
        self,
        file: BinaryIO | SpooledTemporaryFile[bytes] | None = None,
        *,
        filename: str | None = None,
        content_type: str | None = None,
        headers: Headers | None = None,
        size: int = 0,
    ) -> None:
        self._file: BinaryIO | SpooledTemporaryFile[bytes] = (
            file
            if file is not None
            else SpooledTemporaryFile(max_size=_DEFAULT_SPOOL_MAX_SIZE)
        )
        self.filename = filename
        self.content_type = content_type
        self.headers = headers if headers is not None else Headers([])
        self.size = size

    async def read(self, size: int = -1) -> bytes:
        return self._file.read(size)

    async def seek(self, offset: int) -> None:
        self._file.seek(offset)

    async def close(self) -> None:
        self._file.close()

    async def stream(self, chunk_size: int = 65536) -> AsyncIterator[bytes]:
        await self.seek(0)
        while True:
            chunk = await self.read(chunk_size)
            if not chunk:
                break
            yield chunk

    def __repr__(self) -> str:
        return (
            f"UploadFile(filename={self.filename!r}, "
            f"content_type={self.content_type!r}, size={self.size})"
        )
