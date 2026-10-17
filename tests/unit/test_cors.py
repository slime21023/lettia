from typing import Any

import httpx
import pytest

from lettia.core import App, Context, Response
from lettia.middleware import cors

ORIGIN = "https://app.example"


def client(app: App) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


class Calls:
    def __init__(self) -> None:
        self.count = 0


def build(calls: Calls | None = None, **config: Any) -> App:
    async def handler(c: Context) -> Response:
        if calls is not None:
            calls.count += 1
        return c.text(200, "ok")

    app = App()
    app.use(cors(**config))
    app.get("/x", handler)
    return app


def preflight(
    method: str = "POST", headers: str | None = None, origin: str = ORIGIN
) -> dict[str, str]:
    sent = {"origin": origin, "access-control-request-method": method}
    if headers is not None:
        sent["access-control-request-headers"] = headers
    return sent


# --- simple requests ----------------------------------------------------


async def test_cors_ignores_requests_without_an_origin() -> None:
    async with client(build(allow_origins=[ORIGIN])) as http:
        res = await http.get("/x")

    assert res.status_code == 200
    assert "access-control-allow-origin" not in res.headers


async def test_cors_echoes_an_allowed_origin_and_varies_on_it() -> None:
    async with client(build(allow_origins=[ORIGIN, "https://b.example"])) as http:
        res = await http.get("/x", headers={"origin": ORIGIN})

    assert res.headers["access-control-allow-origin"] == ORIGIN
    assert "origin" in res.headers["vary"].lower()


async def test_cors_wildcard_allows_any_origin_with_a_star() -> None:
    async with client(build(allow_origins=["*"])) as http:
        res = await http.get("/x", headers={"origin": "https://anywhere.example"})

    assert res.headers["access-control-allow-origin"] == "*"


async def test_cors_does_not_decorate_a_disallowed_origin() -> None:
    async with client(build(allow_origins=[ORIGIN])) as http:
        res = await http.get("/x", headers={"origin": "https://evil.example"})

    assert res.status_code == 200
    assert "access-control-allow-origin" not in res.headers
    assert "origin" in res.headers["vary"].lower()


async def test_cors_origin_comparison_is_exact() -> None:
    async with client(build(allow_origins=[ORIGIN])) as http:
        res = await http.get("/x", headers={"origin": ORIGIN + ".evil.example"})

    assert "access-control-allow-origin" not in res.headers


async def test_cors_credentials_echo_the_origin_and_allow_credentials() -> None:
    app = build(allow_origins=[ORIGIN], allow_credentials=True)

    async with client(app) as http:
        res = await http.get("/x", headers={"origin": ORIGIN})

    assert res.headers["access-control-allow-origin"] == ORIGIN
    assert res.headers["access-control-allow-credentials"] == "true"


def test_cors_credentials_with_a_wildcard_origin_is_rejected() -> None:
    with pytest.raises(ValueError, match="allow_credentials"):
        cors(allow_origins=["*"], allow_credentials=True)


async def test_cors_exposes_the_configured_response_headers() -> None:
    app = build(allow_origins=[ORIGIN], expose_headers=["x-request-id", "etag"])

    async with client(app) as http:
        res = await http.get("/x", headers={"origin": ORIGIN})

    assert res.headers["access-control-expose-headers"] == "x-request-id, etag"


# --- preflight ----------------------------------------------------------


async def test_cors_answers_a_preflight_without_calling_the_route() -> None:
    calls = Calls()
    app = build(calls, allow_origins=[ORIGIN])

    async with client(app) as http:
        res = await http.options("/x", headers=preflight("POST"))

    assert res.status_code == 204
    assert res.content == b""
    assert calls.count == 0
    assert res.headers["access-control-allow-origin"] == ORIGIN
    assert res.headers["access-control-allow-methods"] == (
        "GET, HEAD, POST, PUT, PATCH, DELETE"
    )
    assert res.headers["access-control-max-age"] == "600"


async def test_cors_preflight_works_for_paths_without_an_options_route() -> None:
    async with client(build(allow_origins=[ORIGIN])) as http:
        no_options_route = await http.options("/x", headers=preflight("GET"))
        unknown_path = await http.options("/nowhere", headers=preflight("GET"))

    assert no_options_route.status_code == 204
    assert unknown_path.status_code == 204


async def test_cors_preflight_uses_configured_methods_and_max_age() -> None:
    app = build(allow_origins=[ORIGIN], allow_methods=["GET", "PUT"], max_age=60)

    async with client(app) as http:
        res = await http.options("/x", headers=preflight("PUT"))

    assert res.headers["access-control-allow-methods"] == "GET, PUT"
    assert res.headers["access-control-max-age"] == "60"


async def test_cors_preflight_rejects_a_method_that_is_not_allowed() -> None:
    app = build(allow_origins=[ORIGIN], allow_methods=["GET"])

    async with client(app) as http:
        res = await http.options("/x", headers=preflight("DELETE"))

    assert res.status_code == 403
    assert "access-control-allow-origin" not in res.headers


async def test_cors_preflight_from_a_disallowed_origin_is_rejected() -> None:
    async with client(build(allow_origins=[ORIGIN])) as http:
        res = await http.options("/x", headers=preflight(origin="https://evil.example"))

    assert res.status_code == 403
    assert "access-control-allow-origin" not in res.headers


async def test_cors_preflight_allows_only_the_configured_request_headers() -> None:
    app = build(allow_origins=[ORIGIN], allow_headers=["Authorization", "X-Token"])

    async with client(app) as http:
        allowed = await http.options(
            "/x", headers=preflight(headers="authorization, x-token")
        )
        refused = await http.options("/x", headers=preflight(headers="x-other"))

    assert allowed.status_code == 204
    assert allowed.headers["access-control-allow-headers"] == "Authorization, X-Token"
    assert refused.status_code == 403


async def test_cors_preflight_reflects_requested_headers_when_star_is_configured() -> (
    None
):
    app = build(allow_origins=[ORIGIN], allow_headers=["*"])

    async with client(app) as http:
        res = await http.options("/x", headers=preflight(headers="x-a, x-b"))

    assert res.headers["access-control-allow-headers"] == "x-a, x-b"


async def test_cors_preflight_with_credentials_allows_credentials() -> None:
    app = build(allow_origins=[ORIGIN], allow_credentials=True)

    async with client(app) as http:
        res = await http.options("/x", headers=preflight())

    assert res.headers["access-control-allow-credentials"] == "true"


async def test_cors_options_without_a_request_method_is_not_a_preflight() -> None:
    app = build(allow_origins=[ORIGIN])

    async with client(app) as http:
        res = await http.options("/x", headers={"origin": ORIGIN})

    assert res.status_code == 405
    assert res.headers["access-control-allow-origin"] == ORIGIN
