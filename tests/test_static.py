import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from lettia.context import Context
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
    ctx = Context(
        scope={"type": "http", "method": "GET", "path": "/static/hello.txt"},
        receive=None,
        send=None,
        path_params={"filepath": "hello.txt"},
    )
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
    ctx = Context(
        scope={"type": "http", "method": "GET", "path": "/static/../etc/passwd"},
        receive=None,
        send=None,
        path_params={"filepath": "../etc/passwd"},
    )
    with pytest.raises(HTTPException) as exc_info:
        await static.handle(ctx)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_static_range_request(temp_static_dir: Path) -> None:
    static = StaticFiles(directory=str(temp_static_dir))

    ctx = Context(
        scope={
            "type": "http",
            "method": "GET",
            "path": "/static/hello.txt",
            "headers": [(b"range", b"bytes=0-4")],
        },
        receive=None,
        send=None,
        path_params={"filepath": "hello.txt"},
    )
    resp = await static.handle(ctx)
    assert resp.status_code == 206
    assert resp.headers["content-range"] == "bytes 0-4/17"

    chunks: list[bytes] = []
    assert resp.async_body is not None
    async for chunk in resp.async_body:
        chunks.append(chunk)
    assert b"".join(chunks) == b"Hello"
