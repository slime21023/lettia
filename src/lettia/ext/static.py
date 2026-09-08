import asyncio
import hashlib
import mimetypes
from collections.abc import AsyncGenerator
from pathlib import Path

from lettia.context import Context
from lettia.errors import abort
from lettia.response import Response, StreamResponse


async def _file_stream(
    path: Path, start: int = 0, length: int | None = None
) -> AsyncGenerator[bytes, None]:
    file = await asyncio.to_thread(path.open, "rb")
    try:
        if start:
            await asyncio.to_thread(file.seek, start)

        remaining = length
        chunk_size = 64 * 1024
        while remaining is None or remaining > 0:
            read_bytes = chunk_size if remaining is None else min(chunk_size, remaining)
            chunk = await asyncio.to_thread(file.read, read_bytes)
            if not chunk:
                break
            if remaining is not None:
                remaining -= len(chunk)
            yield chunk
    finally:
        await asyncio.to_thread(file.close)


class StaticFiles:
    def __init__(self, directory: str, html: bool = False) -> None:
        self.directory: Path = Path(directory).expanduser().resolve()
        self.html: bool = html

        if not self.directory.is_dir():
            raise RuntimeError(f"Directory '{directory}' does not exist")

    async def handle(self, ctx: Context) -> Response:
        if ctx.method not in ("GET", "HEAD"):
            abort(405, "Method Not Allowed")

        filepath = ctx.path_params.get("filepath", "")
        if not filepath:
            filepath = ctx.path.lstrip("/")

        safe_path = await asyncio.to_thread(
            lambda: (self.directory / filepath).resolve()
        )
        try:
            safe_path.relative_to(self.directory)
        except ValueError:
            abort(403, "Forbidden")

        if await asyncio.to_thread(safe_path.is_dir):
            if not self.html:
                abort(404, "Not Found")
            index_path = safe_path / "index.html"
            if not await asyncio.to_thread(index_path.is_file):
                abort(404, "Not Found")
            safe_path = index_path

        if not await asyncio.to_thread(safe_path.is_file):
            abort(404, "Not Found")

        stat_result = await asyncio.to_thread(safe_path.stat)
        file_size = stat_result.st_size
        mtime = int(stat_result.st_mtime)
        etag = f'"{hashlib.sha256(f"{mtime}-{file_size}".encode()).hexdigest()}"'

        if_none_match = ctx.header("if-none-match")
        if if_none_match and if_none_match == etag:
            return Response(status_code=304, headers={"etag": etag})

        media_type, _ = mimetypes.guess_type(str(safe_path))
        if media_type is None:
            media_type = "application/octet-stream"

        headers: dict[str, str] = {
            "etag": etag,
            "accept-ranges": "bytes",
        }

        range_header = ctx.header("range")
        if range_header and range_header.startswith("bytes="):
            try:
                bytes_range = range_header.removeprefix("bytes=").strip()
                if "," in bytes_range:
                    abort(416, "Range Not Satisfiable")
                start_str, end_str = bytes_range.split("-", 1)
                if not start_str and not end_str:
                    abort(416, "Range Not Satisfiable")

                if not start_str:
                    suffix_length = int(end_str)
                    if suffix_length <= 0:
                        abort(416, "Range Not Satisfiable")
                    start = max(file_size - suffix_length, 0)
                    end = file_size - 1
                else:
                    start = int(start_str)
                    end = int(end_str) if end_str else file_size - 1
                    if start >= file_size:
                        abort(416, "Range Not Satisfiable")
                    end = min(end, file_size - 1)

                if start > end:
                    abort(416, "Range Not Satisfiable")

                length = end - start + 1
                headers["content-range"] = f"bytes {start}-{end}/{file_size}"
                headers["content-length"] = str(length)

                return StreamResponse(
                    generator=_file_stream(safe_path, start=start, length=length),
                    status_code=206,
                    headers=headers,
                    media_type=media_type,
                )
            except ValueError:
                abort(416, "Range Not Satisfiable")

        headers["content-length"] = str(file_size)

        return StreamResponse(
            generator=_file_stream(safe_path),
            status_code=200,
            headers=headers,
            media_type=media_type,
        )
