import base64
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import override

import httpx
import pytest

from lettia import App
from lettia.core import Context, Response
from lettia.core.errors import StartupError
from lettia.extensions.docs import Provider, mount_docs, pages

PROVIDERS: list[Provider] = ["scalar", "swagger", "redoc"]


class Page(HTMLParser):
    """What a browser would build from the page: elements and their attributes."""

    def __init__(self, source: str) -> None:
        super().__init__()
        self.scripts: list[dict[str, str | None]] = []
        self.links: list[dict[str, str | None]] = []
        self.title = ""
        self._in_title = False
        self.feed(source)

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.scripts.append(dict(attrs))
        elif tag == "link":
            self.links.append(dict(attrs))
        elif tag == "title":
            self._in_title = True

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    @override
    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def client(app: App) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def docs_app(provider: Provider = "scalar", **options: object) -> App:
    app = App(title="Shop")
    mount_docs(app, "/docs", provider, **options)  # type: ignore[arg-type]
    return app


async def page_of(app: App, path: str = "/docs") -> tuple[httpx.Response, Page]:
    async with client(app) as http:
        res = await http.get(path)
    return res, Page(res.text)


def sri(data: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()


# --- CDN pages ----------------------------------------------------------------


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_cdn_page_pins_and_checks_every_external_file(
    provider: Provider,
) -> None:
    _, page = await page_of(docs_app(provider))

    external = [
        e
        for e in (*page.scripts, *page.links)
        if (e.get("src") or e.get("href") or "").startswith("https://")
    ]
    assert external, "the page loads nothing from a CDN"
    for element in external:
        url = element.get("src") or element.get("href") or ""
        assert re.search(r"@\d+\.\d+\.\d+/", url), f"not pinned to a version: {url}"
        assert re.fullmatch(r"sha384-[A-Za-z0-9+/=]+", element["integrity"] or "")
        assert element["crossorigin"] == "anonymous"


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_page_has_no_csp_when_it_uses_the_cdn(provider: Provider) -> None:
    res, _ = await page_of(docs_app(provider))

    assert "content-security-policy" not in res.headers


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_every_provider_is_served_as_html(provider: Provider) -> None:
    res, page = await page_of(docs_app(provider))

    assert res.status_code == 200
    assert res.headers["content-type"] == "text/html; charset=utf-8"
    assert page.title == "Shop"
    assert "/openapi.json" in res.text


async def test_scalar_page_reads_the_document_and_the_theme() -> None:
    _, page = await page_of(docs_app("scalar", theme="dark"))

    reference = next(s for s in page.scripts if s.get("id") == "api-reference")
    assert reference["data-url"] == "/openapi.json"
    config = json.loads(reference["data-configuration"] or "")
    assert config["forceDarkModeState"] == "dark"
    assert config["hideDarkModeToggle"] is True
    assert config["withDefaultFonts"] is False


async def test_scalar_page_leaves_the_mode_to_the_browser_on_auto() -> None:
    _, page = await page_of(docs_app("scalar"))

    reference = next(s for s in page.scripts if s.get("id") == "api-reference")
    config = json.loads(reference["data-configuration"] or "")
    assert "forceDarkModeState" not in config


async def test_swagger_page_disables_the_remote_validator() -> None:
    res, _ = await page_of(docs_app("swagger"))

    assert "validatorUrl: null" in res.text


# --- hostile text -------------------------------------------------------------

HOSTILE = '"></script><script>alert(1)</script></title><script>alert(2)</script>'


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_hostile_title_and_urls_add_no_elements_to_the_page(
    provider: Provider,
) -> None:
    _, clean = await page_of(docs_app(provider, favicon="/icon"))
    app = App(title=HOSTILE, openapi_url="/x" + HOSTILE)
    mount_docs(app, "/docs", provider, favicon="/icon" + HOSTILE)

    _, page = await page_of(app)

    assert page.title == HOSTILE
    assert len(page.scripts) == len(clean.scripts)
    assert len(page.links) == len(clean.links)


@pytest.mark.parametrize("provider", ["swagger", "redoc"])
async def test_line_separators_cannot_end_a_script_string(provider: Provider) -> None:
    app = App(openapi_url="/a\u2028b\u2029c")
    mount_docs(app, "/docs", provider)

    res, _ = await page_of(app)

    assert "\u2028" not in res.text
    assert "\u2029" not in res.text


# --- through the App ----------------------------------------------------------


async def test_docs_are_off_by_default() -> None:
    async with client(App()) as http:
        assert (await http.get("/docs")).status_code == 404


async def test_docs_url_serves_the_scalar_page_by_default() -> None:
    res, page = await page_of(App(docs_url="/docs", title="Shop"))

    assert res.status_code == 200
    assert page.title == "Shop"
    assert 'data-url="/openapi.json"' in res.text


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_docs_ui_picks_the_provider(provider: Provider) -> None:
    app = App(docs_url="/docs", docs_ui=provider)
    expected = {
        "scalar": "api-reference",
        "swagger": "SwaggerUIBundle",
        "redoc": "Redoc.init",
    }

    res, _ = await page_of(app)

    assert expected[provider] in res.text


async def test_docs_follow_a_custom_openapi_url() -> None:
    res, _ = await page_of(App(docs_url="/docs", openapi_url="/spec.json"))

    assert 'data-url="/spec.json"' in res.text


async def test_head_request_gets_the_headers_without_the_page() -> None:
    async with client(docs_app()) as http:
        res = await http.head("/docs")

    assert res.status_code == 200
    assert res.headers["content-type"] == "text/html; charset=utf-8"
    assert res.content == b""


async def test_app_properties_describe_the_document() -> None:
    assert App(title="Shop", openapi_url="/spec.json").title == "Shop"
    assert App(title="Shop", openapi_url="/spec.json").openapi_url == "/spec.json"
    assert App(openapi_url=None).openapi_url is None


async def test_mount_docs_uses_the_app_title_unless_given_one() -> None:
    _, default = await page_of(docs_app())
    _, custom = await page_of(docs_app(title="Custom"))

    assert default.title == "Shop"
    assert custom.title == "Custom"


async def test_favicon_is_linked() -> None:
    _, page = await page_of(docs_app(favicon="https://example.com/i.png"))

    assert [link["href"] for link in page.links if link.get("rel") == "icon"] == [
        "https://example.com/i.png"
    ]


async def test_prefix_can_be_the_root_or_end_with_a_slash() -> None:
    root = App()
    mount_docs(root, "/")
    slash = App()
    mount_docs(slash, "/ui/")

    assert (await page_of(root, "/"))[0].status_code == 200
    assert (await page_of(slash, "/ui/"))[0].status_code == 200


def test_docs_need_the_openapi_document() -> None:
    with pytest.raises(ValueError, match="openapi_url"):
        App(docs_url="/docs", openapi_url=None)
    with pytest.raises(ValueError, match="openapi_url"):
        mount_docs(App(openapi_url=None))


def test_the_docs_page_is_not_part_of_the_documented_api() -> None:
    assert docs_app().openapi()["paths"] == {}


# --- rejected settings --------------------------------------------------------


def test_an_unknown_docs_ui_is_rejected() -> None:
    with pytest.raises(ValueError, match="provider"):
        App(docs_url="/docs", docs_ui="rapidoc")  # type: ignore[arg-type]


def test_an_unknown_theme_is_rejected() -> None:
    with pytest.raises(ValueError, match="theme"):
        mount_docs(App(), theme="sepia")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "prefix",
    ["docs", "", "/docs?x=1", "/docs#a", "/do\ncs", "/docs/*", "/{id}", "/d\x7f"],
)
def test_an_unsafe_prefix_is_rejected(prefix: str) -> None:
    with pytest.raises(ValueError, match="prefix"):
        mount_docs(App(), prefix)


@pytest.mark.parametrize(
    "favicon",
    [
        "//evil.example/i.png",
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "ftp://example.com/i.png",
        "http:///i.png",
        "/a\\b",
        "/a\nb",
        "i.png",
        "",
    ],
)
def test_an_unsafe_favicon_is_rejected(favicon: str) -> None:
    app = App()

    with pytest.raises(ValueError, match="favicon"):
        mount_docs(app, favicon=favicon)


def test_a_rejected_setting_registers_nothing() -> None:
    app = App()

    with pytest.raises(ValueError):
        mount_docs(app, "/docs", favicon="javascript:alert(1)")
    mount_docs(app, "/docs")  # would clash if the failed call had registered it

    app.build()


def test_mount_after_build_is_a_runtime_error() -> None:
    app = App()
    app.build()

    with pytest.raises(RuntimeError, match="already built"):
        mount_docs(app)


def test_a_second_mount_at_the_same_prefix_fails_the_build() -> None:
    app = App()
    mount_docs(app, "/docs")
    mount_docs(app, "/docs", "swagger")

    with pytest.raises(StartupError):
        app.build()


def test_docs_do_not_replace_an_existing_route() -> None:
    async def mine(c: Context) -> Response:
        return Response(200, b"mine")

    app = App()
    app.get("/docs", mine)
    mount_docs(app, "/docs")

    with pytest.raises(StartupError):
        app.build()


# --- local assets -------------------------------------------------------------


@pytest.fixture
def bundles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand-in files whose hashes replace the pinned ones for the check."""
    pinned = {
        asset.name: asset
        for assets in pages.PROVIDER_ASSETS.values()
        for asset in assets
    }
    for name, asset in pinned.items():
        data = f"/* {name} */".encode()
        (tmp_path / name).write_bytes(data)
        pinned[name] = pages.Asset(name, asset.url, sri(data))
    monkeypatch.setattr(
        pages,
        "PROVIDER_ASSETS",
        {
            provider: tuple(pinned[a.name] for a in assets)
            for provider, assets in pages.PROVIDER_ASSETS.items()
        },
    )
    return tmp_path


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_local_assets_are_served_with_the_page_under_a_strict_policy(
    bundles: Path, provider: Provider
) -> None:
    app = docs_app(provider, assets=bundles)

    res, page = await page_of(app)

    assert res.status_code == 200
    assert "default-src 'self'" in res.headers["content-security-policy"]
    assert "connect-src 'self'" in res.headers["content-security-policy"]
    references = [
        e.get("src") or e.get("href") or "" for e in (*page.scripts, *page.links)
    ]
    local = [r for r in references if r]
    assert local
    async with client(app) as http:
        for reference in local:
            assert reference.startswith("/docs/assets/")
            asset = await http.get(reference)
            assert asset.status_code == 200
            assert asset.content == (bundles / reference.rsplit("/", 1)[1]).read_bytes()


async def test_local_assets_follow_a_trailing_slash_prefix(bundles: Path) -> None:
    app = App()
    mount_docs(app, "/ui/", "redoc", assets=bundles)

    _, page = await page_of(app, "/ui/")

    assert page.scripts[0]["src"] == "/ui/assets/redoc.standalone.js"


async def test_local_assets_do_not_leak_hidden_or_foreign_files(
    bundles: Path,
) -> None:
    (bundles / ".secret").write_text("x")
    app = docs_app("redoc", assets=bundles)

    async with client(app) as http:
        assert (await http.get("/docs/assets/.secret")).status_code == 404
        assert (await http.get("/docs/assets/../x")).status_code == 404


@pytest.mark.parametrize("provider", PROVIDERS)
def test_missing_asset_fails_the_build_without_falling_back_to_the_cdn(
    bundles: Path, provider: Provider
) -> None:
    for path in bundles.iterdir():
        path.unlink()
    app = docs_app(provider, assets=bundles)

    with pytest.raises(StartupError, match="cannot read"):
        app.build()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_modified_asset_fails_the_build(bundles: Path, provider: Provider) -> None:
    for asset in pages.PROVIDER_ASSETS[provider]:
        (bundles / asset.name).write_bytes(b"alert('tampered')")
    app = docs_app(provider, assets=bundles)

    with pytest.raises(StartupError, match="hash does not match"):
        app.build()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_files_that_are_not_the_pinned_bundles_are_rejected(
    tmp_path: Path, provider: Provider
) -> None:
    for assets in pages.PROVIDER_ASSETS.values():
        for asset in assets:
            (tmp_path / asset.name).write_bytes(b"not the real file")
    app = docs_app(provider, assets=tmp_path)

    with pytest.raises(StartupError, match="pinned"):
        app.build()


def test_a_missing_assets_directory_is_a_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        mount_docs(App(), assets=tmp_path / "nope")


def test_a_relative_assets_directory_is_checked_where_it_was_mounted(
    bundles: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = bundles / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(bundles)
    app = docs_app("redoc", assets=".")

    monkeypatch.chdir(elsewhere)  # the working directory moves before the build

    app.build()


def test_a_remote_favicon_is_rejected_with_local_assets(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="same-origin"):
        mount_docs(App(), favicon="https://example.com/i.png", assets=tmp_path)


async def test_assets_routes_exist_only_with_assets() -> None:
    async with client(docs_app()) as http:
        assert (await http.get("/docs/assets/standalone.js")).status_code == 404
