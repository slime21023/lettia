"""The shipped example is a contract, an application and a committed document."""

import json
from pathlib import Path

import httpx
import pytest
from openapi_spec_validator import validate

from examples.users_api import (
    SystemService,
    UserOut,
    UserService,
    admin,
    bearer,
    require_admin,
    verify_bearer,
)
from examples.users_api import (
    app as shipped_app,
)
from lettia import App
from lettia.cli import main

SNAPSHOT = Path(__file__).parents[2] / "examples" / "openapi.json"

USER = {"authorization": "Bearer user-token"}
ADMIN = {"authorization": "Bearer admin-token"}


def fresh_app() -> App:
    """The example's assembly with its own data, so tests do not share state."""
    app = App(
        title="Users API", guards=[bearer.use(verify_bearer), admin.use(require_admin)]
    )
    app.group("/v1").mount(UserService({1: UserOut(1, "Ann", "ann@example.com")}))
    app.mount(SystemService())
    return app


def client(app: App) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


# --- the committed OpenAPI document ---------------------------------------


def test_the_committed_document_matches_what_the_cli_exports(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["openapi", "export", "examples.users_api:app"]) == 0

    exported = capsys.readouterr().out
    assert exported == SNAPSHOT.read_text(encoding="utf-8"), (
        "examples/openapi.json is stale; regenerate it with "
        "`uv run lettia openapi export examples.users_api:app "
        "--output examples/openapi.json`"
    )


def test_the_document_is_valid_openapi_3_1() -> None:
    validate(shipped_app.openapi())


async def test_the_openapi_endpoint_serves_the_committed_document() -> None:
    async with client(shipped_app) as http:
        res = await http.get("/openapi.json")

    assert res.json() == json.loads(SNAPSHOT.read_text(encoding="utf-8"))


# --- the example behaves as its contract says -----------------------------


async def test_a_caller_can_read_users() -> None:
    async with client(fresh_app()) as http:
        listed = await http.get("/v1/users", headers=USER)
        one = await http.get("/v1/users/1", headers=USER)

    assert listed.json() == [{"id": 1, "name": "Ann", "email": "ann@example.com"}]
    assert one.json()["name"] == "Ann"


async def test_requests_without_a_token_are_refused_before_anything_else() -> None:
    async with client(fresh_app()) as http:
        res = await http.post("/v1/users", json={"name": 5})

    assert res.status_code == 401


async def test_only_administrators_may_create_and_delete() -> None:
    async with client(fresh_app()) as http:
        as_user = await http.post(
            "/v1/users", json={"name": "Bo", "email": "bo@example.com"}, headers=USER
        )
        created = await http.post(
            "/v1/users", json={"name": "Bo", "email": "bo@example.com"}, headers=ADMIN
        )
        deleted = await http.delete("/v1/users/2", headers=ADMIN)

    assert as_user.status_code == 403
    assert created.status_code == 201
    assert created.headers["location"] == "/v1/users/2"
    assert deleted.status_code == 204


async def test_missing_users_are_a_declared_404() -> None:
    async with client(fresh_app()) as http:
        res = await http.get("/v1/users/99", headers=USER)

    assert res.status_code == 404
    assert res.json()["title"] == "user not found"


async def test_an_invalid_body_is_reported_with_its_location() -> None:
    async with client(fresh_app()) as http:
        res = await http.post("/v1/users", json={"name": "Bo"}, headers=ADMIN)

    assert res.status_code == 422
    assert res.json()["errors"][0]["path"] == "$.body.email"


async def test_the_health_probe_needs_no_credentials() -> None:
    async with client(fresh_app()) as http:
        res = await http.get("/healthz")

    assert (res.status_code, res.json()) == (200, "ok")
