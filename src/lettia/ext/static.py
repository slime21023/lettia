import asyncio
import hashlib
import mimetypes
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import NoReturn
from urllib.parse import quote, quote_from_bytes

from lettia._conditional import if_none_match, select_byte_range
from lettia.asgi import _route_path  # pyright: ignore[reportPrivateUsage]
from lettia.context import Context
from lettia.errors import abort
from lettia.response import Response, StreamResponse


async def _file_stream(
    path: Path, start: int = 0, length: int | None = None
) -> AsyncGenerator[bytes, None]:
    # Resolve the binary overload before passing the callable to to_thread.
    opening = asyncio.create_task(asyncio.to_thread(lambda: path.open("rb")))
    try:
        file = await asyncio.shield(opening)
    except asyncio.CancelledError:
        # A worker thread keeps running after cancellation. Reclaim its file
        # before propagating a disconnect or timeout to the response writer.
        file = await opening
        await asyncio.to_thread(file.close)
        raise
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


def _content_etag(path: Path) -> str:
    with path.open("rb") as file:
        return f'"{hashlib.file_digest(file, "sha256").hexdigest()}"'


def _range_not_satisfiable(file_size: int) -> NoReturn:
    abort(
        416,
        "Range Not Satisfiable",
        headers={"Content-Range": f"bytes */{file_size}"},
    )


class StaticFiles:
    def __init__(self, directory: str, html: bool = False) -> None:
        self.directory: Path = Path(directory).expanduser().resolve()
        self.html: bool = html

        if not self.directory.is_dir():
            raise RuntimeError(f"Directory '{directory}' does not exist")

    async def handle(self, ctx: Context) -> Response:
        if ctx.method not in ("GET", "HEAD"):
            abort(405, "Method Not Allowed")

        filepath = ctx.path_params.get("filepath", _route_path(ctx.scope).lstrip("/"))

        safe_path = await asyncio.to_thread(
            lambda: (self.directory / filepath).resolve()
        )
        try:
            safe_path.relative_to(self.directory)
        except ValueError:
            abort(403, "Forbidden")

        is_directory = await asyncio.to_thread(safe_path.is_dir)
        if is_directory:
            if not self.html:
                abort(404, "Not Found")
            safe_path = await asyncio.to_thread((safe_path / "index.html").resolve)
            if not safe_path.is_relative_to(self.directory):
                abort(403, "Forbidden")

        if not await asyncio.to_thread(safe_path.is_file):
            abort(404, "Not Found")

        if is_directory and not ctx.path.endswith("/"):
            # Use an origin-relative URL, even for paths beginning with //.
            external_path = ctx.scope.get("root_path", "").rstrip("/") + _route_path(
                ctx.scope
            )
            location = quote("/" + external_path.strip("/") + "/", safe="/")
            query = ctx.scope["query_string"]
            if query:
                location += "?" + quote_from_bytes(query, safe="!$&'()*+,-./:;=?@_%~")
            return Response(status_code=307, headers={"location": location})

        stat_result = await asyncio.to_thread(safe_path.stat)
        file_size = stat_result.st_size
        etag = await asyncio.to_thread(_content_etag, safe_path)

        validator = ctx.header("if-none-match")
        if validator and if_none_match(validator, etag):
            return Response(status_code=304, headers={"etag": etag})

        media_type, _ = mimetypes.guess_type(str(safe_path))
        if media_type is None:
            media_type = "application/octet-stream"

        headers: dict[str, str] = {
            "etag": etag,
            "accept-ranges": "bytes",
        }

        try:
            selected = select_byte_range(
                ctx.method,
                ctx.header("range"),
                ctx.header("if-range"),
                etag,
                file_size,
            )
        except ValueError:
            _range_not_satisfiable(file_size)
        if selected is not None:
            start, end = selected
            length = end - start + 1
            headers["content-range"] = f"bytes {start}-{end}/{file_size}"
            headers["content-length"] = str(length)

            return StreamResponse(
                generator=_file_stream(safe_path, start=start, length=length),
                status_code=206,
                headers=headers,
                media_type=media_type,
            )

        headers["content-length"] = str(file_size)

        return StreamResponse(
            generator=_file_stream(safe_path),
            status_code=200,
            headers=headers,
            media_type=media_type,
        )
