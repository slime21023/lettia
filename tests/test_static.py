import asyncio
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from asgi_helpers import (
    http_context,
    http_receive,
    http_scope,
    http_sender,
    response_body,
)
from hypothesis import example, given, settings
from hypothesis import strategies as st
from strategies import PAYLOADS, SEGMENTS

from lettia import App
from lettia.asgi import HTTPSendEvent
from lettia.errors import HTTPException
from lettia.ext import StaticFiles


@given(payload=PAYLOADS, prefix=SEGMENTS, head=st.booleans())
@settings(deadline=None)
async def test_static_mount_serves_index_and_files(
    payload: bytes,
    prefix: str,
    head: bool,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "index.html").write_bytes(payload)
        (root / "file.bin").write_bytes(payload)
        app = App()
        app.add_route(
            "GET", f"/{prefix}/*filepath", StaticFiles(directory, html=True).handle
        )
        for tail in ("", "file.bin"):
            sent: list[HTTPSendEvent] = []
            await app(
                http_scope(method="HEAD" if head else "GET", path=f"/{prefix}/{tail}"),
                http_receive([]),
                http_sender(sent),
            )
            start = sent[0]
            assert start["type"] == "http.response.start" and start["status"] == 200
            assert (
                dict(start["headers"])[b"content-length"] == str(len(payload)).encode()
            )
            assert response_body(sent) == (b"" if head else payload)


@given(
    payload=st.binary(min_size=1, max_size=4096),
    first=st.integers(0, 5000),
    length=st.integers(1, 5000),
    suffix=st.booleans(),
)
@settings(deadline=None)
async def test_static_ranges_match_byte_slices(
    payload: bytes,
    first: int,
    length: int,
    suffix: bool,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "file.bin").write_bytes(payload)
        static = StaticFiles(directory)
        start = first % len(payload)
        range_value = (
            f"bytes=-{length}" if suffix else f"bytes={start}-{start + length - 1}"
        )
        expected = payload[-length:] if suffix else payload[start : start + length]
        response = await static.handle(
            http_context(path="/file.bin", headers=[(b"range", range_value.encode())])
        )
        assert response.status_code == 206
        sent: list[HTTPSendEvent] = []
        from lettia.response import ResponseWriter

        await ResponseWriter(http_sender(sent)).write(response)
        assert response_body(sent) == expected
        assert response.headers["content-length"] == str(len(expected))


@given(payload=st.binary(min_size=1, max_size=4096))
@settings(deadline=None)
@example(payload=b"x")
async def test_etag_tracks_content_even_when_metadata_is_unchanged(
    payload: bytes,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "file.bin"
        path.write_bytes(payload)
        metadata = path.stat()
        static = StaticFiles(directory)
        first = await static.handle(http_context(path="/file.bin"))
        modified = bytes([payload[0] ^ 1]) + payload[1:]
        path.write_bytes(modified)
        os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        changed = await static.handle(
            http_context(
                path="/file.bin",
                headers=[(b"if-none-match", first.headers["etag"].encode())],
            )
        )
        assert changed.status_code == 200
        assert changed.headers["etag"] != first.headers["etag"]
        unchanged = await static.handle(
            http_context(
                path="/file.bin",
                headers=[(b"if-none-match", changed.headers["etag"].encode())],
            )
        )
        assert unchanged.status_code == 304
        from lettia.response import ResponseWriter

        sent: list[HTTPSendEvent] = []
        await ResponseWriter(http_sender(sent)).write(unchanged)
        assert response_body(sent) == b""
        start = sent[0]
        assert start["type"] == "http.response.start"
        assert b"content-length" not in dict(start["headers"])


@given(name=SEGMENTS, payload=PAYLOADS)
@settings(deadline=None)
async def test_static_rejects_paths_outside_root(name: str, payload: bytes) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "public"
        root.mkdir()
        outside = Path(directory) / (name + ".txt")
        outside.write_bytes(payload)
        static = StaticFiles(str(root), html=True)
        for candidate in ("../" + outside.name, str(outside)):
            ctx = http_context()
            ctx.path_params = {"filepath": candidate}
            with pytest.raises(HTTPException) as error:
                await static.handle(ctx)
            assert error.value.status_code == 403


@given(payload=PAYLOADS, index=st.booleans())
@settings(deadline=None)
async def test_static_rejects_symlink_escape_including_index(
    payload: bytes, index: bool
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "public"
        root.mkdir()
        outside = Path(directory) / "outside.txt"
        outside.write_bytes(payload)
        link = root / ("index.html" if index else "file.txt")
        try:
            link.symlink_to(outside)
        except OSError as exc:
            if os.name == "nt" and exc.winerror == 1314:
                pytest.skip("Windows user lacks symbolic-link privilege")
            raise
        with pytest.raises(HTTPException) as error:
            await StaticFiles(str(root), html=True).handle(
                http_context(path="/" if index else "/file.txt")
            )
        assert error.value.status_code == 403


@given(
    range_value=st.sampled_from(
        [
            "bytes=0-1,3-4",
            "bytes=-",
            "bytes=100-101",
            "bytes=4-1",
            "bytes=-0",
            "bytes=x-y",
        ]
    ),
    html=st.booleans(),
)
@settings(deadline=None)
async def test_static_boundary_errors(range_value: str, html: bool) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "file.txt").write_bytes(b"hello")
        static = StaticFiles(directory, html=html)
        for ctx, status in [
            (http_context(method="POST"), 405),
            (http_context(path="/missing"), 404),
            (http_context(path="/"), 404),
            (
                http_context(
                    path="/file.txt", headers=[(b"range", range_value.encode())]
                ),
                416,
            ),
        ]:
            with pytest.raises(HTTPException) as error:
                await static.handle(ctx)
            assert error.value.status_code == status
        with pytest.raises(RuntimeError):
            StaticFiles(str(root / "missing"))


async def test_static_file_operations_are_offloaded() -> None:
    original = asyncio.to_thread
    calls: list[object] = []

    async def track[T](func: Callable[..., T], /, *args: object, **kwargs: object) -> T:
        calls.append(func)
        return await original(func, *args, **kwargs)

    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "file.txt").write_bytes(b"hello")
        with patch("lettia.ext.static.asyncio.to_thread", track):
            response = await StaticFiles(directory).handle(
                http_context(path="/file.txt")
            )
            assert response.async_body is not None
            assert b"".join([chunk async for chunk in response.async_body]) == b"hello"
        assert calls
