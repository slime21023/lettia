import os
import tempfile
from pathlib import Path
from urllib.parse import urljoin

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lettia import App
from lettia.asgi import (
    HTTPSendEvent,
)
from lettia.errors import HTTPException
from lettia.ext import StaticFiles
from lettia.testing import TestClient
from tests.support.asgi import (
    http_context,
    http_receive,
    http_scope,
    http_sender,
    response_body,
)
from tests.support.strategies import PAYLOADS, SEGMENTS


@pytest.mark.contract("STATIC-PATH")
@pytest.mark.parametrize("wildcard", [False, True])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("prefix_in_path", [False, True])
async def test_mounted_static_files_keep_redirect_prefix_and_resolve_files(
    tmp_path: Path,
    wildcard: bool,
    method: str,
    prefix_in_path: bool,
) -> None:
    directory = tmp_path / "pages"
    directory.mkdir()
    (directory / "index.html").write_text("index", encoding="utf-8")
    static = StaticFiles(str(tmp_path), html=True)
    app = App()
    if wildcard:
        app.get("/*filepath")(static.handle)
    else:
        app.get("/pages")(static.handle)
        app.get("/pages/")(static.handle)

    redirect_scope = http_scope(
        method=method,
        path="/api/pages" if prefix_in_path else "/pages",
        query_string=b"lang=en",
    )
    redirect_scope["root_path"] = "/api"
    sent: list[HTTPSendEvent] = []
    await app(redirect_scope, http_receive([]), http_sender(sent))
    start = sent[0]
    assert start["type"] == "http.response.start" and start["status"] == 307
    assert dict(start["headers"])[b"location"] == b"/api/pages/?lang=en"

    file_scope = http_scope(
        method=method, path="/api/pages/" if prefix_in_path else "/pages/"
    )
    file_scope["root_path"] = "/api"
    sent.clear()
    await app(file_scope, http_receive([]), http_sender(sent))
    assert response_body(sent) == (b"index" if method == "GET" else b"")
    start = sent[0]
    assert start["type"] == "http.response.start" and start["status"] == 200


@pytest.mark.contract("STATIC-PATH")
@pytest.mark.asyncio
async def test_static_files_rejects_sibling_directory_escape(tmp_path: Path) -> None:
    root = tmp_path / "www"
    sibling = tmp_path / "www-evil"
    root.mkdir()
    sibling.mkdir()
    (sibling / "secret.txt").write_text("secret", encoding="utf-8")
    ctx = http_context(path="/static/secret.txt")
    ctx.path_params = {"filepath": "../www-evil/secret.txt"}
    with pytest.raises(HTTPException, match="Forbidden") as exc_info:
        await StaticFiles(str(root)).handle(ctx)
    assert exc_info.value.status_code == 403


@pytest.mark.contract("STATIC-PATH")
@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("prefix", ["", "/assets"])
def test_static_directory_redirect_preserves_query_and_relative_assets(
    tmp_path: Path, method: str, prefix: str
) -> None:
    directory = tmp_path / "docs"
    directory.mkdir()
    (directory / "index.html").write_text('<link href="style.css">', encoding="utf-8")
    (directory / "style.css").write_bytes(b"body {}")
    app = App()
    app.add_route(
        "GET", f"{prefix}/*filepath", StaticFiles(str(tmp_path), html=True).handle
    )
    client = TestClient(app)

    result = client.request(
        method,
        f"{prefix}/docs?x=a%2Fb&x=2",
        headers={"range": "bytes=0-0", "if-none-match": "*"},
    )

    assert result.status_code == 307
    assert result.headers["location"] == f"{prefix}/docs/?x=a%2Fb&x=2"
    assert result.content == b""
    canonical = client.request(method, result.headers["location"])
    assert canonical.status_code == 200
    assert bool(canonical.content) == (method == "GET")
    asset = client.get(urljoin(str(canonical.url), "style.css"))
    assert asset.status_code == 200 and asset.content == b"body {}"


@pytest.mark.contract("STATIC-PATH")
async def test_static_redirect_quotes_path_and_cannot_change_origin(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "文件 #1"
    directory.mkdir()
    (directory / "index.html").write_bytes(b"ok")
    ctx = http_context(path="//文件 #1", query_string=b"next=%2Fhome&bad=\r\n")
    ctx.path_params["filepath"] = "文件 #1"

    response = await StaticFiles(str(tmp_path), html=True).handle(ctx)

    assert response.status_code == 307
    assert (
        response.headers["location"]
        == "/%E6%96%87%E4%BB%B6%20%231/?next=%2Fhome&bad=%0D%0A"
    )


@pytest.mark.contract("STATIC-PATH")
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


@pytest.mark.contract("STATIC-PATH")
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


@pytest.mark.contract("STATIC-PATH")
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


@pytest.mark.contract("STATIC-PATH")
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
