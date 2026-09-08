import asyncio
import tempfile
from collections.abc import Callable, Generator
from pathlib import Path

import pytest
from asgi_helpers import http_context

from lettia.errors import HTTPException
from lettia.ext.static import StaticFiles


@pytest.fixture
def temp_static_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        dir_path = Path(tmpdir)
        (dir_path / "hello.txt").write_text("Hello Static File")
        (dir_path / "index.html").write_text("<h1>Index Page</h1>")
        sub_dir = dir_path / "sub"
        sub_dir.mkdir()
        (sub_dir / "app.js").write_text("console.log('test');")
        yield dir_path


@pytest.mark.asyncio
async def test_static_files_serving(temp_static_dir: Path) -> None:
    static = StaticFiles(directory=str(temp_static_dir))

    # Test file serving
    ctx = http_context(method="GET", path="/static/hello.txt")
    ctx.path_params = {"filepath": "hello.txt"}
    resp = await static.handle(ctx)
    assert resp.status_code == 200
    assert resp.headers["content-length"] == "17"

    # Stream body read
    chunks: list[bytes] = []
    assert resp.async_body is not None
    async for chunk in resp.async_body:
        chunks.append(chunk)
    assert b"".join(chunks) == b"Hello Static File"


@pytest.mark.asyncio
async def test_static_path_traversal_prevention(temp_static_dir: Path) -> None:
    static = StaticFiles(directory=str(temp_static_dir))

    from lettia.errors import HTTPException

    # Test ../ attack
    ctx = http_context(method="GET", path="/static/../etc/passwd")
    ctx.path_params = {"filepath": "../etc/passwd"}
    with pytest.raises(HTTPException) as exc_info:
        await static.handle(ctx)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_static_range_request(temp_static_dir: Path) -> None:
    static = StaticFiles(directory=str(temp_static_dir))

    ctx = http_context(
        method="GET",
        path="/static/hello.txt",
        headers=[(b"range", b"bytes=0-4")],
    )
    ctx.path_params = {"filepath": "hello.txt"}
    resp = await static.handle(ctx)
    assert resp.status_code == 206
    assert resp.headers["content-range"] == "bytes 0-4/17"

    chunks: list[bytes] = []
    assert resp.async_body is not None
    async for chunk in resp.async_body:
        chunks.append(chunk)
    assert b"".join(chunks) == b"Hello"


@pytest.mark.asyncio
async def test_static_file_operations_are_offloaded(
    temp_static_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static = StaticFiles(directory=str(temp_static_dir))
    original_to_thread = asyncio.to_thread
    calls: list[object] = []

    async def tracking_to_thread[T](
        func: Callable[..., T], /, *args: object, **kwargs: object
    ) -> T:
        calls.append(func)
        return await original_to_thread(func, *args, **kwargs)

    monkeypatch.setattr("lettia.ext.static.asyncio.to_thread", tracking_to_thread)
    ctx = http_context(method="GET", path="/static/hello.txt")
    ctx.path_params = {"filepath": "hello.txt"}

    response = await static.handle(ctx)
    assert response.async_body is not None
    assert (
        b"".join([chunk async for chunk in response.async_body]) == b"Hello Static File"
    )
    assert calls


def test_static_files_requires_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="does not exist"):
        StaticFiles(str(tmp_path / "missing"))


@pytest.mark.asyncio
async def test_static_files_rejects_unsupported_methods(temp_static_dir: Path) -> None:
    with pytest.raises(HTTPException, match="Method Not Allowed") as exc_info:
        await StaticFiles(str(temp_static_dir)).handle(http_context(method="POST"))
    assert exc_info.value.status_code == 405


@pytest.mark.asyncio
async def test_static_files_handles_directories_and_missing_paths(
    temp_static_dir: Path,
) -> None:
    without_html = StaticFiles(str(temp_static_dir))
    with pytest.raises(HTTPException) as directory_error:
        await without_html.handle(http_context(path="/sub"))
    assert directory_error.value.status_code == 404

    with_html = StaticFiles(str(temp_static_dir), html=True)
    index = await with_html.handle(http_context(path="/"))
    assert index.status_code == 200
    assert index.async_body is not None
    assert (
        b"".join([chunk async for chunk in index.async_body]) == b"<h1>Index Page</h1>"
    )

    with pytest.raises(HTTPException) as missing_error:
        await with_html.handle(http_context(path="/missing.txt"))
    assert missing_error.value.status_code == 404


@pytest.mark.asyncio
async def test_static_files_etag_and_invalid_ranges(temp_static_dir: Path) -> None:
    static = StaticFiles(str(temp_static_dir))
    first = await static.handle(http_context(path="/hello.txt"))
    etag = first.headers["etag"]
    assert first.async_body is not None
    _ = [chunk async for chunk in first.async_body]

    not_modified = await static.handle(
        http_context(path="/hello.txt", headers=[(b"if-none-match", etag.encode())])
    )
    assert not_modified.status_code == 304

    for range_value in (
        b"bytes=0-1,3-4",
        b"bytes=-",
        b"bytes=100-101",
        b"bytes=4-1",
        b"bytes=-0",
        b"bytes=x-y",
    ):
        with pytest.raises(HTTPException) as range_error:
            await static.handle(
                http_context(path="/hello.txt", headers=[(b"range", range_value)])
            )
        assert range_error.value.status_code == 416
