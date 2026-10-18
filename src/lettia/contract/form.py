"""Multipart and urlencoded form support, and the UploadFile container."""

import urllib.parse
from collections.abc import AsyncIterator
from tempfile import SpooledTemporaryFile
from typing import BinaryIO

from lettia.core import Headers, HTTPError, Request

_DEFAULT_SPOOL_MAX_SIZE = 1024 * 1024  # 1 MiB in memory before spilling to disk


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
            else SpooledTemporaryFile(max_size=_DEFAULT_SPOOL_MAX_SIZE)
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
        key, sep, val = param.strip().partition("=")
        if sep and key.strip().lower() == "boundary":
            boundary = val.strip()
            if boundary.startswith('"') and boundary.endswith('"'):
                boundary = boundary[1:-1]
            return boundary
    return ""


async def _parse_multipart(
    stream: AsyncIterator[bytes], boundary: str
) -> tuple[dict[str, list[str]], list[UploadFile]]:
    stream_iter = aiter(stream)
    buf = bytearray()
    initial_delim = b"--" + boundary.encode("latin-1")
    part_delim = b"\r\n--" + boundary.encode("latin-1")
    delim_len = len(part_delim)
    form_data: dict[str, list[str]] = {}
    files: list[UploadFile] = []

    try:
        # 1. Seek past initial boundary
        while True:
            idx = buf.find(initial_delim)
            if idx != -1:
                del buf[: idx + len(initial_delim)]
                break
            try:
                buf.extend(await anext(stream_iter))
            except StopAsyncIteration:
                raise HTTPError(
                    400,
                    "Bad Request",
                    "unexpected EOF waiting for initial multipart boundary",
                ) from None

        # Check if empty multipart: immediately `--`
        while len(buf) < 2:
            try:
                buf.extend(await anext(stream_iter))
            except StopAsyncIteration:
                raise HTTPError(
                    400, "Bad Request", "unexpected EOF after initial boundary"
                ) from None

        if bytes(buf[:2]) == b"--":
            return {}, []
        if bytes(buf[:2]) == b"\r\n":
            del buf[:2]

        # 2. Loop parsing each part
        while True:
            # Read headers
            while True:
                header_end = buf.find(b"\r\n\r\n")
                if header_end != -1:
                    raw_headers = bytes(buf[:header_end])
                    del buf[: header_end + 4]
                    break
                try:
                    buf.extend(await anext(stream_iter))
                except StopAsyncIteration:
                    raise HTTPError(
                        400, "Bad Request", "unexpected EOF reading multipart headers"
                    ) from None

            headers = _parse_part_headers(raw_headers)
            disp = headers.get("content-disposition", "")
            name, filename = _parse_content_disposition(disp)
            part_content_type = headers.get("content-type")

            is_file = filename is not None
            upload: UploadFile | None = None
            text_acc: bytearray | None = None
            if is_file:
                upload = UploadFile(
                    filename=filename,
                    content_type=part_content_type,
                    headers=headers,
                    name=name,
                )
            else:
                text_acc = bytearray()

            # Read part body until part_delim
            while True:
                idx = buf.find(part_delim)
                if idx != -1:
                    chunk = bytes(buf[:idx])
                    if upload is not None:
                        upload.write(chunk)
                    elif text_acc is not None:
                        text_acc.extend(chunk)
                    del buf[: idx + delim_len]
                    break

                if len(buf) > delim_len:
                    flush_len = len(buf) - delim_len
                    chunk = bytes(buf[:flush_len])
                    if upload is not None:
                        upload.write(chunk)
                    elif text_acc is not None:
                        text_acc.extend(chunk)
                    del buf[:flush_len]

                try:
                    buf.extend(await anext(stream_iter))
                except StopAsyncIteration:
                    raise HTTPError(
                        400, "Bad Request", "unexpected EOF reading multipart part body"
                    ) from None

            # Finalize part
            if upload is not None:
                await upload.seek(0)
                files.append(upload)
            elif text_acc is not None:
                value = text_acc.decode("utf-8", errors="replace")
                form_data.setdefault(name, []).append(value)

            # Check next 2 bytes after delimiter
            while len(buf) < 2:
                try:
                    buf.extend(await anext(stream_iter))
                except StopAsyncIteration:
                    raise HTTPError(
                        400, "Bad Request", "unexpected EOF after multipart boundary"
                    ) from None

            if bytes(buf[:2]) == b"--":
                break
            if bytes(buf[:2]) == b"\r\n":
                del buf[:2]
                continue
            raise HTTPError(400, "Bad Request", "malformed multipart boundary line")

        return form_data, files
    except Exception:
        for f in files:
            await f.close()
        raise


def _parse_part_headers(raw: bytes) -> Headers:
    lines = raw.split(b"\r\n")
    pairs: list[tuple[bytes, bytes]] = []
    for line in lines:
        if not line:
            continue
        name, sep, val = line.partition(b":")
        if sep:
            pairs.append((name.strip().lower(), val.strip()))
    return Headers(pairs)


def _parse_content_disposition(value: str) -> tuple[str, str | None]:
    name = ""
    filename = None
    parts = value.split(";")
    for part in parts[1:]:
        part = part.strip()
        if not part:
            continue
        key, sep, val = part.partition("=")
        if not sep:
            continue
        key = key.strip().lower()
        val = val.strip()
        if key == "name":
            name = _decode_header_str(val.strip('"'))
        elif key == "filename":
            filename = _decode_header_str(val.strip('"'))
        elif key == "filename*" and filename is None:
            _, _, encoded = val.partition("''")
            filename = urllib.parse.unquote(encoded or val.strip('"'))
    return name, filename


def _decode_header_str(value: str) -> str:
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
