"""Multipart and urlencoded form support, and the UploadFile container."""

import urllib.parse
from collections.abc import AsyncIterator, Callable
from tempfile import SpooledTemporaryFile
from typing import BinaryIO

from lettia.core import Headers, HTTPError, Request

_DEFAULT_SPOOL_MAX_BYTES = 1024 * 1024  # 1 MiB in memory before spilling to disk


class UploadFile:
    """An uploaded file buffered in memory or on disk.

    Small files (up to 1 MiB) stay in memory; larger files spill to a temporary
    file on disk.
    """

    __slots__ = ("_file", "content_type", "filename", "headers", "name", "size")

    def __init__(
        self,
        file: BinaryIO | SpooledTemporaryFile[bytes] | None = None,
        *,
        filename: str | None = None,
        content_type: str | None = None,
        headers: Headers | None = None,
        name: str | None = None,
        size: int = 0,
    ) -> None:
        self._file: BinaryIO | SpooledTemporaryFile[bytes] = (
            file
            if file is not None
            else SpooledTemporaryFile(max_size=_DEFAULT_SPOOL_MAX_BYTES)
        )
        self.filename = filename
        self.content_type = content_type
        self.headers = headers if headers is not None else Headers([])
        self.name = name
        self.size = size

    @property
    def rolled_to_disk(self) -> bool:
        """True if the file has spilled over from memory to a disk file."""
        return getattr(self._file, "_rolled", False)

    def write(self, chunk: bytes) -> None:
        self._file.write(chunk)
        self.size += len(chunk)

    async def read(self, size: int = -1) -> bytes:
        return self._file.read(size)

    async def seek(self, offset: int) -> None:
        self._file.seek(offset)

    async def close(self) -> None:
        self._file.close()

    async def stream(
        self, chunk_size: int = 65536, *, chunk_size_bytes: int | None = None
    ) -> AsyncIterator[bytes]:
        size_bytes = chunk_size_bytes if chunk_size_bytes is not None else chunk_size
        await self.seek(0)
        while True:
            chunk = await self.read(size_bytes)
            if not chunk:
                break
            yield chunk

    def __repr__(self) -> str:
        return (
            f"UploadFile(filename={self.filename!r}, "
            f"content_type={self.content_type!r}, size={self.size})"
        )


async def parse_form_data(
    request: Request,
) -> tuple[dict[str, list[str]], list[UploadFile]]:
    """Parse form data (urlencoded or multipart) from an incoming request."""
    content_type_header = request.headers.get("content-type")
    if content_type_header is None:
        body = await request.body()
        if not body:
            return {}, []
        text = body.decode("utf-8", errors="replace")
        parsed = urllib.parse.parse_qs(text, keep_blank_values=True)
        return parsed, []

    media, _, params_raw = content_type_header.partition(";")
    media = media.strip().lower()

    if media == "application/x-www-form-urlencoded":
        body = await request.body()
        text = body.decode("utf-8", errors="replace")
        parsed = urllib.parse.parse_qs(text, keep_blank_values=True)
        return parsed, []

    if media == "multipart/form-data":
        boundary = _extract_boundary(params_raw)
        if not boundary:
            raise HTTPError(
                400, "Bad Request", "missing boundary in multipart/form-data"
            )
        return await _parse_multipart(request.stream(), boundary)

    raise HTTPError(
        415,
        "Unsupported Media Type",
        f"expected form data or multipart, got {media!r}",
    )


def _extract_boundary(params_raw: str) -> str:
    for param in params_raw.split(";"):
        param_key, separator, param_value = param.strip().partition("=")
        if separator and param_key.strip().lower() == "boundary":
            boundary = param_value.strip()
            if boundary.startswith('"') and boundary.endswith('"'):
                boundary = boundary[1:-1]
            return boundary
    return ""


class _MultipartReader:
    __slots__ = ("_buffer", "_chunks")

    def __init__(self, stream: AsyncIterator[bytes]) -> None:
        self._buffer = bytearray()
        self._chunks = aiter(stream)

    async def _fill(self, eof_detail: str) -> None:
        try:
            self._buffer.extend(await anext(self._chunks))
        except StopAsyncIteration:
            raise HTTPError(400, "Bad Request", eof_detail) from None

    async def skip_past(self, marker: bytes, eof_detail: str) -> None:
        while True:
            index = self._buffer.find(marker)
            if index != -1:
                del self._buffer[: index + len(marker)]
                return
            await self._fill(eof_detail)

    async def read_until(self, marker: bytes, eof_detail: str) -> bytes:
        while True:
            index = self._buffer.find(marker)
            if index != -1:
                chunk = bytes(self._buffer[:index])
                del self._buffer[: index + len(marker)]
                return chunk
            await self._fill(eof_detail)

    async def copy_until(
        self, delimiter: bytes, sink: Callable[[bytes], object], eof_detail: str
    ) -> None:
        # Why: part_delimiter starts with CRLF (\r\n--), acting as part terminator.
        keep = len(delimiter)
        while True:
            index = self._buffer.find(delimiter)
            if index != -1:
                sink(bytes(self._buffer[:index]))
                del self._buffer[: index + keep]
                return
            if len(self._buffer) > keep:
                flush = len(self._buffer) - keep
                sink(bytes(self._buffer[:flush]))
                del self._buffer[:flush]
            await self._fill(eof_detail)

    async def at_final_boundary(self, eof_detail: str) -> bool:
        # Why: After delimiter, '--' indicates end of payload; CRLF means next part.
        while len(self._buffer) < 2:
            await self._fill(eof_detail)
        suffix = bytes(self._buffer[:2])
        if suffix == b"--":
            return True
        if suffix == b"\r\n":
            del self._buffer[:2]
            return False
        raise HTTPError(400, "Bad Request", "malformed multipart boundary line")


async def _read_part(
    reader: _MultipartReader,
    delimiter: bytes,
    form_data: dict[str, list[str]],
    files: list[UploadFile],
) -> None:
    raw_headers = await reader.read_until(
        b"\r\n\r\n", "unexpected EOF reading multipart headers"
    )
    headers = _parse_part_headers(raw_headers)
    field_name, filename = _parse_content_disposition(
        headers.get("content-disposition", "")
    )
    part_eof = "unexpected EOF reading multipart part body"
    if filename is None:
        text_buffer = bytearray()
        await reader.copy_until(delimiter, text_buffer.extend, part_eof)
        form_data.setdefault(field_name, []).append(
            text_buffer.decode("utf-8", errors="replace")
        )
        return
    upload = UploadFile(
        filename=filename,
        content_type=headers.get("content-type"),
        headers=headers,
        name=field_name,
    )
    await reader.copy_until(delimiter, upload.write, part_eof)
    await upload.seek(0)
    files.append(upload)


async def _parse_multipart(
    stream: AsyncIterator[bytes], boundary: str
) -> tuple[dict[str, list[str]], list[UploadFile]]:
    reader = _MultipartReader(stream)
    boundary_bytes = boundary.encode("latin-1")
    part_delimiter = b"\r\n--" + boundary_bytes
    form_data: dict[str, list[str]] = {}
    files: list[UploadFile] = []
    try:
        # Why: RFC 7578 initial boundary is '--' + boundary after optional preamble.
        await reader.skip_past(
            b"--" + boundary_bytes,
            "unexpected EOF waiting for initial multipart boundary",
        )
        at_end = await reader.at_final_boundary("unexpected EOF after initial boundary")
        while not at_end:
            await _read_part(reader, part_delimiter, form_data, files)
            at_end = await reader.at_final_boundary(
                "unexpected EOF after multipart boundary"
            )
        return form_data, files
    except BaseException:
        for upload_file in files:
            await upload_file.close()
        raise


def _parse_part_headers(raw_headers: bytes) -> Headers:
    lines = raw_headers.split(b"\r\n")
    pairs: list[tuple[bytes, bytes]] = []
    for line in lines:
        if not line:
            continue
        header_name, separator, header_value = line.partition(b":")
        if separator:
            pairs.append((header_name.strip().lower(), header_value.strip()))
    return Headers(pairs)


def _parse_content_disposition(disposition_header: str) -> tuple[str, str | None]:
    name = ""
    filename = None
    parts = disposition_header.split(";")
    for part in parts[1:]:
        part = part.strip()
        if not part:
            continue
        param_key, separator, param_value = part.partition("=")
        if not separator:
            continue
        param_key = param_key.strip().lower()
        param_value = param_value.strip()
        if param_key == "name":
            name = _decode_header_str(param_value.strip('"'))
        elif param_key == "filename":
            filename = _decode_header_str(param_value.strip('"'))
        elif param_key == "filename*" and filename is None:
            # Why: RFC 5987 encodes charset''language''filename format
            _, _, encoded = param_value.partition("''")
            filename = urllib.parse.unquote(encoded or param_value.strip('"'))
    return name, filename


def _decode_header_str(value: str) -> str:
    # Why: RFC 2047 and browsers transmit non-ASCII headers in ISO-8859-1 wrapper
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
