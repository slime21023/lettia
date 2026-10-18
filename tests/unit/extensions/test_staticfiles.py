"""Unit tests for lettia.extensions.staticfiles."""

from pathlib import Path

import pytest

from lettia import App
from lettia.extensions.staticfiles import StaticFiles, parse_range_header
from lettia.testing import TestClient


def test_parse_range_header_unit() -> None:
    # 1. Normal ranges
    r1 = parse_range_header("bytes=0-499", 1000)
    assert r1 != "ignore" and r1 != "unsatisfiable"
    assert r1.start == 0 and r1.end == 499 and r1.length == 500
    assert r1.content_range == "bytes 0-499/1000"

    # 2. Open-ended range
    r2 = parse_range_header("bytes=500-", 1000)
    assert r2 != "ignore" and r2 != "unsatisfiable"
    assert r2.start == 500 and r2.end == 999 and r2.length == 500

    # 3. Suffix range
    r3 = parse_range_header("bytes=-200", 1000)
    assert r3 != "ignore" and r3 != "unsatisfiable"
    assert r3.start == 800 and r3.end == 999 and r3.length == 200

    # 4. End clamped to file size
    r4 = parse_range_header("bytes=900-2000", 1000)
    assert r4 != "ignore" and r4 != "unsatisfiable"
    assert r4.start == 900 and r4.end == 999 and r4.length == 100

    # 5. Unsatisfiable range
    assert parse_range_header("bytes=1500-2000", 1000) == "unsatisfiable"
    assert parse_range_header("bytes=500-200", 1000) == "unsatisfiable"
    assert parse_range_header("bytes=-0", 1000) == "unsatisfiable"
    assert parse_range_header("bytes=0-10", 0) == "unsatisfiable"

    # 6. Ignored formats (fallback to 200 OK)
    assert parse_range_header(None, 1000) == "ignore"
    assert parse_range_header("characters=0-10", 1000) == "ignore"
    assert parse_range_header("bytes=0-10, 20-30", 1000) == "ignore"
    assert parse_range_header("bytes=abc-def", 1000) == "ignore"


def test_invalid_directory_raises_value_error(tmp_path: Path) -> None:
    non_existent = tmp_path / "does_not_exist"
    with pytest.raises(ValueError, match="does not exist"):
        _ = StaticFiles(non_existent)


@pytest.mark.asyncio
async def test_serve_file_returns_200_and_mime_type(tmp_path: Path) -> None:
    css_file = tmp_path / "theme.css"
    css_file.write_text("body { background: #000; }", encoding="utf-8")

    app = App()
    StaticFiles.mount(app, prefix="/static", directory=tmp_path)

    async with TestClient(app) as client:
        resp = await client.get("/static/theme.css")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/css; charset=utf-8"
        assert resp.headers["accept-ranges"] == "bytes"
        assert int(resp.headers["content-length"]) == len(css_file.read_bytes())
        assert resp.text == "body { background: #000; }"
        assert "etag" in resp.headers
        assert "last-modified" in resp.headers


@pytest.mark.asyncio
async def test_serve_large_file_streams_chunks(tmp_path: Path) -> None:
    # 10 KB file with chunk size 2 KB
    large_data = b"X" * 10240
    data_file = tmp_path / "data.bin"
    data_file.write_bytes(large_data)

    app = App()
    StaticFiles.mount(app, prefix="/files", directory=tmp_path, chunk_size_bytes=2048)

    async with TestClient(app) as client:
        resp = await client.get("/files/data.bin")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"
        assert int(resp.headers["content-length"]) == 10240
        assert resp.content == large_data


@pytest.mark.asyncio
async def test_http_caching_etag_and_if_none_match(tmp_path: Path) -> None:
    doc = tmp_path / "info.txt"
    doc.write_text("cache test content", encoding="utf-8")

    app = App()
    StaticFiles.mount(app, prefix="/static", directory=tmp_path)

    async with TestClient(app) as client:
        # First request
        resp1 = await client.get("/static/info.txt")
        assert resp1.status_code == 200
        etag = resp1.headers["etag"]
        last_modified = resp1.headers["last-modified"]

        # If-None-Match matches ETag -> 304 Not Modified
        resp2 = await client.get("/static/info.txt", headers={"if-none-match": etag})
        assert resp2.status_code == 304
        assert resp2.content == b""

        # If-Modified-Since matches -> 304 Not Modified
        resp3 = await client.get(
            "/static/info.txt", headers={"if-modified-since": last_modified}
        )
        assert resp3.status_code == 304
        assert resp3.content == b""


@pytest.mark.asyncio
async def test_range_request_partial_content_returns_206(tmp_path: Path) -> None:
    sample_file = tmp_path / "sample.txt"
    sample_file.write_text("0123456789abcdef", encoding="utf-8")  # 16 bytes

    app = App()
    StaticFiles.mount(app, prefix="/static", directory=tmp_path, chunk_size_bytes=8)

    async with TestClient(app) as client:
        # 1. Explicit range 0-4 (5 bytes)
        resp1 = await client.get("/static/sample.txt", headers={"range": "bytes=0-4"})
        assert resp1.status_code == 206
        assert resp1.headers["content-range"] == "bytes 0-4/16"
        assert resp1.headers["content-length"] == "5"
        assert resp1.text == "01234"

        # 2. Open-ended range 10- (6 bytes: abcdef)
        resp2 = await client.get("/static/sample.txt", headers={"range": "bytes=10-"})
        assert resp2.status_code == 206
        assert resp2.headers["content-range"] == "bytes 10-15/16"
        assert resp2.headers["content-length"] == "6"
        assert resp2.text == "abcdef"

        # 3. Suffix range -4 (last 4 bytes: cdef)
        resp3 = await client.get("/static/sample.txt", headers={"range": "bytes=-4"})
        assert resp3.status_code == 206
        assert resp3.headers["content-range"] == "bytes 12-15/16"
        assert resp3.headers["content-length"] == "4"
        assert resp3.text == "cdef"


@pytest.mark.asyncio
async def test_range_request_unsatisfiable_returns_416(tmp_path: Path) -> None:
    sample_file = tmp_path / "short.txt"
    sample_file.write_text("hello", encoding="utf-8")  # 5 bytes

    app = App()
    StaticFiles.mount(app, prefix="/static", directory=tmp_path)

    async with TestClient(app) as client:
        resp = await client.get("/static/short.txt", headers={"range": "bytes=100-200"})
        assert resp.status_code == 416
        assert resp.headers["content-range"] == "bytes */5"


@pytest.mark.asyncio
async def test_path_traversal_attack_blocked_with_404(tmp_path: Path) -> None:
    pub_dir = tmp_path / "public"
    pub_dir.mkdir()
    secret = tmp_path / "secret.env"
    secret.write_text("SECRET_KEY=12345", encoding="utf-8")

    app = App()
    StaticFiles.mount(app, prefix="/static", directory=pub_dir)

    async with TestClient(app) as client:
        resp1 = await client.get("/static/../secret.env")
        assert resp1.status_code == 404

        resp2 = await client.get("/static/%2e%2e/secret.env")
        assert resp2.status_code == 404

        resp3 = await client.get("/static/test.txt::$DATA")
        assert resp3.status_code == 404


@pytest.mark.asyncio
async def test_hidden_files_blocked_with_404(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("PRIVATE_CONFIG=true", encoding="utf-8")

    app = App()
    StaticFiles.mount(app, prefix="/static", directory=tmp_path)

    async with TestClient(app) as client:
        resp = await client.get("/static/.env")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_spa_fallback_serves_index_html(tmp_path: Path) -> None:
    index_file = tmp_path / "index.html"
    index_file.write_text("<html><body>SPA Root</body></html>", encoding="utf-8")

    bundle_file = tmp_path / "bundle.js"
    bundle_file.write_text("console.log('spa');", encoding="utf-8")

    app = App()
    StaticFiles.mount(app, prefix="/app", directory=tmp_path, html=True)

    async with TestClient(app) as client:
        # Existing file returns file
        resp_bundle = await client.get("/app/bundle.js")
        assert resp_bundle.status_code == 200
        assert resp_bundle.text == "console.log('spa');"

        # SPA route (e.g. /app/dashboard/settings) returns index.html
        resp_spa = await client.get("/app/dashboard/settings")
        assert resp_spa.status_code == 200
        assert "<html><body>SPA Root</body></html>" in resp_spa.text

        # Explicit missing asset (e.g. /app/missing-logo.png) returns 404
        resp_missing_asset = await client.get("/app/missing-logo.png")
        assert resp_missing_asset.status_code == 404


@pytest.mark.asyncio
async def test_mount_serves_root_directory_index(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text("<h1>Home</h1>", encoding="utf-8")

    app = App()
    StaticFiles.mount(app, prefix="/docs", directory=tmp_path)

    async with TestClient(app) as client:
        # Accessing /docs (without slash) serves index.html
        resp1 = await client.get("/docs")
        assert resp1.status_code == 200
        assert resp1.text == "<h1>Home</h1>"

        # Accessing /docs/index.html serves index.html
        resp2 = await client.get("/docs/index.html")
        assert resp2.status_code == 200
        assert resp2.text == "<h1>Home</h1>"
