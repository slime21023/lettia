"""Static files serving with path safety, caching, range requests and SPA support."""

import asyncio
import email.utils
import mimetypes
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Self

from lettia.core import App, Context, HTTPError, Response
from lettia.core.headers import MutableHeaders
from lettia.extensions.staticfiles.range import ByteRange, parse_range_header

# Known asset extensions that should not fallback to index.html under SPA routing.
_ASSET_EXTENSIONS = frozenset(
    {
        ".avif",
        ".css",
        ".eot",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".js",
        ".json",
        ".map",
        ".mjs",
        ".mp3",
        ".mp4",
        ".ogg",
        ".pdf",
        ".png",
        ".svg",
        ".ttf",
        ".wasm",
        ".wav",
        ".webm",
        ".webp",
        ".woff",
        ".woff2",
        ".xml",
    }
)


class StaticFiles:
    """Serve static files from a local directory.

    Features:
    - Path traversal and symlink escape defense.
    - Dotfiles / hidden files protection.
    - RFC 7232 conditional caching (ETag / If-None-Match / If-Modified-Since).
    - RFC 7233 Range requests (HTTP 206 Partial Content).
    - Asynchronous streaming for files >= chunk_size_bytes via asyncio.to_thread.
    - SPA (Single Page Application) HTML fallback support (html=True).
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        html: bool = False,
        cache_control: str | None = "public, max-age=3600",
        chunk_size_bytes: int = 64 * 1024,
        follow_symlinks: bool = False,
        allow_hidden: bool = False,
        index_name: str = "index.html",
    ) -> None:
        self._directory = Path(directory).resolve()
        if not self._directory.exists() or not self._directory.is_dir():
            raise ValueError(
                f"StaticFiles directory {str(directory)!r} does not exist "
                "or is not a directory"
            )
        self._html = html
        self._cache_control = cache_control
        self._chunk_size_bytes = chunk_size_bytes
        self._follow_symlinks = follow_symlinks
        self._allow_hidden = allow_hidden
        self._index_name = index_name

    @classmethod
    def mount(
        cls,
        app: App,
        prefix: str,
        directory: str | Path,
        *,
        html: bool = False,
        cache_control: str | None = "public, max-age=3600",
        chunk_size_bytes: int = 64 * 1024,
        follow_symlinks: bool = False,
        allow_hidden: bool = False,
        index_name: str = "index.html",
    ) -> Self:
        """Helper to register this StaticFiles handler under an App path prefix."""
        handler = cls(
            directory=directory,
            html=html,
            cache_control=cache_control,
            chunk_size_bytes=chunk_size_bytes,
            follow_symlinks=follow_symlinks,
            allow_hidden=allow_hidden,
            index_name=index_name,
        )
        clean_prefix = "/" + prefix.strip("/")
        app.group(clean_prefix).get("/*", handler)
        app.get(clean_prefix, handler)
        return handler

    async def __call__(self, c: Context) -> Response:
        """Core Handler entry point."""
        subpath = c.path_params.get("*", "").strip("/\\")

        # 1. Reject Windows Alternate Data Streams and null bytes
        if ":" in subpath or "\x00" in subpath:
            raise HTTPError(404, "Not Found")

        # 2. Reject hidden files if not allowed
        if not self._allow_hidden and subpath:
            for part in Path(subpath).parts:
                if part.startswith(".") and part not in (".", ".."):
                    raise HTTPError(404, "Not Found")

        # 3. Resolve target file path and check boundaries
        target = (self._directory / subpath).resolve()
        if not target.is_relative_to(self._directory):
            raise HTTPError(404, "Not Found")

        # 4. Check symlink policy
        if not self._follow_symlinks:
            if not self._is_symlink_safe(target):
                raise HTTPError(404, "Not Found")

        # 5. Handle directory / index lookup
        if target.is_dir():
            index_file = target / self._index_name
            if index_file.is_file():
                target = index_file
            elif self._html:
                fallback_index = self._directory / self._index_name
                if fallback_index.is_file():
                    target = fallback_index
                else:
                    raise HTTPError(404, "Not Found")
            else:
                raise HTTPError(404, "Not Found")

        # 6. Handle file missing & SPA fallback
        if not target.is_file():
            if self._html and target.suffix.lower() not in _ASSET_EXTENSIONS:
                fallback_index = self._directory / self._index_name
                if fallback_index.is_file():
                    target = fallback_index
                else:
                    raise HTTPError(404, "Not Found")
            else:
                raise HTTPError(404, "Not Found")

        # 7. File attributes & HTTP caching
        stat_result = await asyncio.to_thread(target.stat)
        file_size = stat_result.st_size
        mtime_ns = stat_result.st_mtime_ns
        mtime_sec = stat_result.st_mtime

        etag = f'W/"{mtime_ns:x}-{file_size:x}"'
        last_modified = email.utils.formatdate(mtime_sec, usegmt=True)

        headers = MutableHeaders()
        headers.set("accept-ranges", "bytes")
        headers.set("etag", etag)
        headers.set("last-modified", last_modified)
        if self._cache_control:
            headers.set("cache-control", self._cache_control)

        mime_type, _ = mimetypes.guess_type(target.name)
        if mime_type is None:
            mime_type = "application/octet-stream"
        elif mime_type.startswith("text/") or mime_type in (
            "application/javascript",
            "application/json",
            "application/xml",
        ):
            if "charset=" not in mime_type:
                mime_type = f"{mime_type}; charset=utf-8"
        headers.set("content-type", mime_type)

        # 8. Check Conditional Cache Headers (304 Not Modified)
        req_headers = c.request.headers
        if req_headers.get("if-none-match") == etag:
            return Response(304, headers=headers)

        if_modified_since = req_headers.get("if-modified-since")
        if if_modified_since:
            try:
                ims_dt = email.utils.parsedate_to_datetime(if_modified_since)
                if int(ims_dt.timestamp()) >= int(mtime_sec):
                    return Response(304, headers=headers)
            except (TypeError, ValueError, OverflowError):
                pass

        # 9. Check Range Request (RFC 7233)
        range_header = req_headers.get("range")
        if range_header:
            range_res = parse_range_header(range_header, file_size)
            if range_res == "unsatisfiable":
                headers.set("content-range", f"bytes */{file_size}")
                return Response(416, headers=headers)
            if isinstance(range_res, ByteRange):
                return await self._serve_range(target, range_res, headers)

        # 10. Serve Full File (200 OK)
        headers.set("content-length", str(file_size))
        if file_size < self._chunk_size_bytes:
            data = await asyncio.to_thread(target.read_bytes)
            return Response(200, body=data, headers=headers)

        stream = self._stream_file(target, offset=0, count=file_size)
        return Response(200, body=stream, headers=headers)

    def _is_symlink_safe(self, path: Path) -> bool:
        """Check whether path or any of its parents is a symlink."""
        curr = path
        while curr != self._directory and curr != curr.parent:
            if curr.is_symlink():
                return False
            curr = curr.parent
        return not self._directory.is_symlink()

    async def _serve_range(
        self, target: Path, byte_range: ByteRange, headers: MutableHeaders
    ) -> Response:
        """Serve a 206 Partial Content response for the given byte range."""
        headers.set("content-range", byte_range.content_range)
        headers.set("content-length", str(byte_range.length))

        if byte_range.length < self._chunk_size_bytes:

            def _read_slice() -> bytes:
                with open(target, "rb") as f:
                    f.seek(byte_range.start)
                    return f.read(byte_range.length)

            data = await asyncio.to_thread(_read_slice)
            return Response(206, body=data, headers=headers)

        stream = self._stream_file(
            target, offset=byte_range.start, count=byte_range.length
        )
        return Response(206, body=stream, headers=headers)

    async def _stream_file(
        self, target: Path, offset: int, count: int
    ) -> AsyncIterator[bytes]:
        """Asynchronously stream chunks of bytes from a file using asyncio.to_thread."""
        f = await asyncio.to_thread(open, target, "rb")
        try:
            if offset > 0:
                await asyncio.to_thread(f.seek, offset)

            remaining = count
            while remaining > 0:
                to_read = min(self._chunk_size_bytes, remaining)
                chunk = await asyncio.to_thread(f.read, to_read)
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            await asyncio.to_thread(f.close)
