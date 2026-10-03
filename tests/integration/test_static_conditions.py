import os
import tempfile
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from lettia import App
from lettia.asgi import (
    HTTPSendEvent,
)
from lettia.ext import StaticFiles
from lettia.testing import TestClient
from tests.support.asgi import (
    http_context,
    http_sender,
    response_body,
)


@pytest.mark.contract("STATIC-CONDITIONAL")
@pytest.mark.asyncio
async def test_static_files_supports_suffix_ranges(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("Hello Static File", encoding="utf-8")
    ctx = http_context(path="/hello.txt", headers=[(b"range", b"bytes=-4")])
    response = await StaticFiles(str(tmp_path)).handle(ctx)
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 13-16/17"
    assert response.async_body is not None
    assert b"".join([chunk async for chunk in response.async_body]) == b"File"


@pytest.mark.contract("STATIC-CONDITIONAL")
@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize(
    "condition,expected",
    [
        ("*", 304),
        ("{etag}", 304),
        ("W/{etag}", 304),
        ('"old,tag", W/{etag}, "other"', 304),
        (" , W/{etag}, , ", 304),
        ('"other"', 200),
        ("{etag}, invalid", 200),
        ('{etag} "other"', 200),
        ('"unterminated', 200),
        ("*, {etag}", 200),
    ],
)
def test_static_if_none_match_precedes_ranges(
    tmp_path: Path,
    method: str,
    condition: str,
    expected: int,
) -> None:
    (tmp_path / "file.txt").write_bytes(b"hello")
    app = App()
    app.add_route("GET", "/*filepath", StaticFiles(str(tmp_path)).handle)
    client = TestClient(app)
    etag = client.get("/file.txt").headers["etag"]

    response = client.request(
        method,
        "/file.txt",
        headers={
            "If-None-Match": condition.format(etag=etag),
            "Range": "bytes=999-",
            "If-Range": '"outdated"',
        },
    )

    assert response.status_code == expected
    assert response.content == (
        b"hello" if expected == 200 and method == "GET" else b""
    )
    if expected == 304:
        assert "content-length" not in response.headers
    else:
        assert response.headers["content-length"] == "5"


@pytest.mark.contract("STATIC-CONDITIONAL")
@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize(
    "validator",
    [None, "{etag}", " W/{etag} ", '"old"', "bad", "", "Sat, 01 Jan 2000 00:00:00 GMT"],
)
def test_static_if_range_requires_matching_strong_etag(
    tmp_path: Path,
    method: str,
    validator: str | None,
) -> None:
    (tmp_path / "file.txt").write_bytes(b"hello")
    app = App()
    app.add_route("GET", "/*filepath", StaticFiles(str(tmp_path)).handle)
    client = TestClient(app)
    etag = client.get("/file.txt").headers["etag"]
    headers = {"Range": "bytes=1-3"}
    if validator is not None:
        headers["If-Range"] = validator.format(etag=etag)

    response = client.request(method, "/file.txt", headers=headers)

    partial = method == "GET" and validator in (None, "{etag}")
    assert response.status_code == (206 if partial else 200)
    assert response.headers["content-length"] == ("3" if partial else "5")
    assert response.headers.get("content-range") == ("bytes 1-3/5" if partial else None)
    assert response.content == (
        b"" if method == "HEAD" else b"ell" if partial else b"hello"
    )


@pytest.mark.contract("STATIC-CONDITIONAL")
@pytest.mark.parametrize("payload", [b"", b"hello"])
@pytest.mark.parametrize(
    "range_value",
    ["bytes=999-", "bytes=x-y"],
)
def test_static_unsatisfiable_range_includes_representation_size(
    tmp_path: Path,
    payload: bytes,
    range_value: str,
) -> None:
    (tmp_path / "file.bin").write_bytes(payload)
    app = App()
    app.add_route("GET", "/*filepath", StaticFiles(str(tmp_path)).handle)

    response = TestClient(app).get("/file.bin", headers={"Range": range_value})

    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(payload)}"


@pytest.mark.contract("STATIC-CONDITIONAL")
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


@pytest.mark.contract("STATIC-CONDITIONAL")
@given(
    payload=st.binary(min_size=1, max_size=4096),
    condition=st.sampled_from(["*", "{etag}", "W/{etag}", '"other,tag", {etag}']),
)
@settings(deadline=None)
@example(payload=b"x", condition="W/{etag}")
async def test_etag_tracks_content_even_when_metadata_is_unchanged(
    payload: bytes,
    condition: str,
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
                headers=[
                    (
                        b"if-none-match",
                        condition.format(etag=changed.headers["etag"]).encode(),
                    )
                ],
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
