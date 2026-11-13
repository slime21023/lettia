"""Static files serving with path safety, caching, range requests and SPA support."""

import asyncio
import email.utils
import mimetypes
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Self

from lettia.core import App, Context, HTTPError, Response
from lettia.core.headers import Headers, MutableHeaders
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

_ENTITY_TAG = re.compile(r'(?:W/)?"[^"]*"')


def _matches_etag(if_none_match: str, etag: str) -> bool:
    """Whether an ``If-None-Match`` value (``*`` or a list of tags) matches ``etag``.

    The weak comparison of RFC 9110 applies: the ``W/`` prefix is ignored.
    """
    if if_none_match.strip() == "*":
        return True
    current = etag.removeprefix("W/")
    return any(
        tag.removeprefix("W/") == current for tag in _ENTITY_TAG.findall(if_none_match)
    )


def _content_type(name: str) -> str:
    mime_type, _ = mimetypes.guess_type(name)
    if mime_type is None:
        return "application/octet-stream"
    text_like = mime_type.startswith("text/") or mime_type in (
        "application/javascript",
        "application/json",
        "application/xml",
    )
    if text_like and "charset=" not in mime_type:
        return f"{mime_type}; charset=utf-8"
    return mime_type


def _not_modified(headers: Headers, etag: str, mtime: float) -> bool:
    if_none_match = headers.get_all("if-none-match")
    if if_none_match:
        # If-Modified-Since is ignored whenever If-None-Match is present.
        return _matches_etag(", ".join(if_none_match), etag)
    if_modified_since = headers.get("if-modified-since")
    if not if_modified_since:
        return False
    try:
        since = email.utils.parsedate_to_datetime(if_modified_since)
    except (TypeError, ValueError, OverflowError):
        return False
    return int(since.timestamp()) >= int(mtime)


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
        self._root = Path(directory).absolute()
        self._directory = self._root.resolve()
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
        if clean_prefix == "/":
            app.get("/*", handler)
            app.get("/", handler)
        else:
            app.group(clean_prefix).get("/*", handler)
            app.get(clean_prefix, handler)
        return handler

    async def __call__(self, c: Context) -> Response:
        """Core Handler entry point."""
        target = self._resolve(c.path_params.get("*", "").strip("/\\"))
        stat: os.stat_result = await asyncio.to_thread(target.stat)
        etag = f'W/"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        headers = self._headers(target, etag, stat.st_mtime)
        request_headers = c.request.headers
        if _not_modified(request_headers, etag, stat.st_mtime):
            return Response(304, headers=headers)
        range_header = request_headers.get("range")
        if range_header:
            byte_range = parse_range_header(range_header, stat.st_size)
            if byte_range == "unsatisfiable":
                headers.set("content-range", f"bytes */{stat.st_size}")
                return Response(416, headers=headers)
            if isinstance(byte_range, ByteRange):
                return await self._serve_range(target, byte_range, headers)
        headers.set("content-length", str(stat.st_size))
        if stat.st_size < self._chunk_size_bytes:
            data = await asyncio.to_thread(target.read_bytes)
            return Response(200, body=data, headers=headers)
        return Response(
            200,
            body=self._stream_file(target, offset=0, count=stat.st_size),
            headers=headers,
        )

    def _is_servable(self, subpath: str) -> bool:
        if ":" in subpath or "\x00" in subpath:
            return False
        if self._allow_hidden or not subpath:
            return True
        return not any(
            part.startswith(".") and part not in (".", "..")
            for part in Path(subpath).parts
        )

    def _spa_index(self) -> Path:
        if self._html:
            fallback = self._directory / self._index_name
            if fallback.is_file():
                return fallback
        raise HTTPError(404, "Not Found")

    def _resolve(self, subpath: str) -> Path:
        if not self._is_servable(subpath):
            raise HTTPError(404, "Not Found")
        target = (self._directory / subpath).resolve()
        if not target.is_relative_to(self._directory):
            raise HTTPError(404, "Not Found")
        if not self._follow_symlinks and not self._is_symlink_safe(subpath):
            raise HTTPError(404, "Not Found")
        if target.is_dir():
            index_file = target / self._index_name
            return index_file if index_file.is_file() else self._spa_index()
        if target.is_file():
            return target
        if target.suffix.lower() in _ASSET_EXTENSIONS:
            raise HTTPError(404, "Not Found")
        return self._spa_index()

    def _headers(self, target: Path, etag: str, mtime: float) -> MutableHeaders:
        headers = MutableHeaders()
        headers.set("accept-ranges", "bytes")
        headers.set("etag", etag)
        headers.set("last-modified", email.utils.formatdate(mtime, usegmt=True))
        if self._cache_control:
            headers.set("cache-control", self._cache_control)
        headers.set("content-type", _content_type(target.name))
        return headers

    def _is_symlink_safe(self, subpath: str) -> bool:
        """Whether ``directory`` and its ``subpath`` components are not symlinks."""
        if self._root.is_symlink():
            return False
        current = self._root
        for part in Path(subpath).parts:
            if part == "..":
                if current == self._root:
                    return False
                current = current.parent
                continue
            current = current / part
            if current.is_symlink():
                return False
        return True

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
