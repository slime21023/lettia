import re
from collections.abc import Callable

import httpx
import pytest

from lettia import App
from lettia.docs import scalar_page, swagger_ui_page

type PageBuilder = Callable[[str, str], str]

PAGES: list[PageBuilder] = [swagger_ui_page, scalar_page]


@pytest.mark.parametrize("page", PAGES)
def test_every_external_script_and_stylesheet_is_pinned_and_checked(
    page: PageBuilder,
) -> None:
    html = page("/openapi.json", "Demo")

    tags = re.findall(r"<(?:script|link)[^>]+(?:src|href)=\"https://[^>]+>", html)

    assert tags, "the page loads nothing from a CDN"
    for tag in tags:
        assert re.search(r"@\d+\.\d+\.\d+/", tag), f"not pinned to a version: {tag}"
        assert "@latest" not in tag
        assert re.search(r'integrity="sha384-[A-Za-z0-9+/=]+"', tag), tag
        assert 'crossorigin="anonymous"' in tag


def test_swagger_ui_page_points_at_the_document() -> None:
    html = swagger_ui_page("/spec.json", "Demo")

    assert 'url: "/spec.json"' in html
    assert "SwaggerUIBundle" in html
    assert "<title>Demo</title>" in html


def test_scalar_page_points_at_the_document() -> None:
    html = scalar_page("/spec.json", "Demo")

    assert 'data-url="/spec.json"' in html
    assert 'id="api-reference"' in html
    assert "<title>Demo</title>" in html


@pytest.mark.parametrize("page", PAGES)
def test_the_title_is_escaped(page: PageBuilder) -> None:
    html = page("/openapi.json", "</title><script>alert(1)</script>")

    assert "<script>alert(1)</script>" not in html
    assert "&lt;/title&gt;" in html


@pytest.mark.parametrize("page", PAGES)
def test_the_document_url_cannot_break_out_of_the_page(page: PageBuilder) -> None:
    html = page('/x"></script><script>alert(1)</script>', "Demo")

    assert "<script>alert(1)</script>" not in html
    assert '/x"></script>' not in html


# --- through an app ---------------------------------------------------------


def client(app: App) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def test_docs_are_off_by_default() -> None:
    async with client(App()) as http:
        assert (await http.get("/docs")).status_code == 404


async def test_docs_url_serves_the_scalar_page_by_default() -> None:
    async with client(App(docs_url="/docs", title="Shop")) as http:
        res = await http.get("/docs")

    assert res.status_code == 200
    assert res.headers["content-type"] == "text/html; charset=utf-8"
    assert 'data-url="/openapi.json"' in res.text
    assert "<title>Shop</title>" in res.text


async def test_docs_ui_can_be_swagger() -> None:
    async with client(App(docs_url="/docs", docs_ui="swagger")) as http:
        res = await http.get("/docs")

    assert "SwaggerUIBundle" in res.text


async def test_docs_follow_a_custom_openapi_url() -> None:
    async with client(App(docs_url="/docs", openapi_url="/spec.json")) as http:
        res = await http.get("/docs")

    assert 'data-url="/spec.json"' in res.text


def test_docs_need_the_openapi_document() -> None:
    with pytest.raises(ValueError, match="openapi_url"):
        App(docs_url="/docs", openapi_url=None)


def test_an_unknown_docs_ui_is_rejected() -> None:
    with pytest.raises(ValueError, match="docs_ui"):
        App(docs_url="/docs", docs_ui="redoc")  # type: ignore[arg-type]


def test_the_docs_page_is_not_part_of_the_documented_api() -> None:
    document = App(docs_url="/docs").openapi()

    assert "/docs" not in document["paths"]
