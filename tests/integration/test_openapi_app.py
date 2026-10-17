"""The OpenAPI document as seen through an application."""

import json
from typing import Protocol

import httpx
import msgspec
import pytest

from lettia import App, Context, GuardSpec, NoContent, Ok, Path, op, public
from lettia.core import Context as CoreContext
from lettia.core import Handler, Response, StartupError


class ItemOut(msgspec.Struct):
    id: int


class GetItemReq(msgspec.Struct):
    item_id: Path[int]


class Admin: ...


admin = GuardSpec[Admin]("admin")


class ItemApi(Protocol):
    @op.get("/items/{item_id}", guard=public, tags=["items"])
    async def get_item(self, c: Context[None], req: GetItemReq) -> Ok[ItemOut]:
        """Get one item."""
        ...


class PingApi(Protocol):
    @op.get("/ping", guard=public)
    async def ping(self, c: Context[None], req: None) -> NoContent: ...


class ItemService(ItemApi):
    async def get_item(self, c: Context[None], req: GetItemReq) -> Ok[ItemOut]:
        return Ok(ItemOut(req.item_id))


class PingService(PingApi):
    async def ping(self, c: Context[None], req: None) -> NoContent:
        return NoContent()


def client(app: App) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def build_app(**options: object) -> App:
    app = App(**options)  # type: ignore[arg-type]
    app.group("/v1").mount(ItemService())
    app.group("/v1").group("/system").mount(PingService())
    return app


# --- the document -------------------------------------------------------


def test_openapi_describes_the_mounted_operations_under_their_prefixes() -> None:
    document = build_app().openapi()

    assert sorted(document["paths"]) == ["/v1/items/{item_id}", "/v1/system/ping"]
    assert document["paths"]["/v1/items/{item_id}"]["get"]["operationId"] == "get_item"


def test_openapi_info_comes_from_the_app_options() -> None:
    document = build_app(title="Shop", version="2.0", description="Items").openapi()

    assert document["info"] == {
        "title": "Shop",
        "version": "2.0",
        "description": "Items",
    }


def test_openapi_has_default_title_and_version() -> None:
    assert build_app().openapi()["info"] == {"title": "API", "version": "0.0.0"}


def test_openapi_builds_the_app_on_demand() -> None:
    app = build_app()

    app.openapi()

    with pytest.raises(RuntimeError, match="built"):
        app.mount(PingService())


def test_openapi_returns_a_copy_the_caller_may_change() -> None:
    app = build_app()

    app.openapi()["info"]["title"] = "tampered"

    assert app.openapi()["info"]["title"] == "API"


def test_openapi_does_not_document_its_own_endpoint() -> None:
    assert "/openapi.json" not in build_app().openapi()["paths"]


def test_the_same_contracts_give_byte_identical_documents() -> None:
    first = json.dumps(build_app().openapi())
    second = json.dumps(build_app().openapi())

    assert first == second


# --- the endpoint -------------------------------------------------------


async def test_openapi_json_is_served_and_matches_the_document() -> None:
    app = build_app()

    async with client(app) as http:
        res = await http.get("/openapi.json")

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/json"
    assert res.json() == app.openapi()


async def test_openapi_json_answers_head_requests() -> None:
    async with client(build_app()) as http:
        res = await http.head("/openapi.json")

    assert (res.status_code, res.content) == (200, b"")


async def test_openapi_url_can_be_changed() -> None:
    async with client(build_app(openapi_url="/spec.json")) as http:
        assert (await http.get("/spec.json")).status_code == 200
        assert (await http.get("/openapi.json")).status_code == 404


async def test_openapi_url_none_turns_the_endpoint_off() -> None:
    async with client(build_app(openapi_url=None)) as http:
        assert (await http.get("/openapi.json")).status_code == 404


async def test_app_middleware_applies_to_the_openapi_endpoint() -> None:
    def tag(next_: Handler) -> Handler:
        async def run(c: CoreContext) -> Response:
            response = await next_(c)
            response.headers.set("x-tag", "yes")
            return response

        return run

    app = build_app()
    app.use(tag)

    async with client(app) as http:
        res = await http.get("/openapi.json")

    assert res.headers["x-tag"] == "yes"


# --- startup ------------------------------------------------------------


def test_document_problems_are_reported_with_the_other_startup_problems() -> None:
    other = msgspec.defstruct("ItemOut", [("id", str)])

    class ClashApi(Protocol):
        @op.get("/clash", guard=public)
        async def clash(self, c: Context[None], req: None) -> Ok[other]: ...  # type: ignore[valid-type]

    class ClashService(ClashApi):
        async def clash(self, c: Context[None], req: None) -> Ok[other]:  # type: ignore[valid-type]
            return Ok(other(id="x"))

    class Guarded(Protocol):
        @op.get("/guarded", guard=admin)
        async def guarded(self, c: Context[Admin], req: None) -> NoContent: ...

    class GuardedService(Guarded):
        async def guarded(self, c: Context[Admin], req: None) -> NoContent:
            return NoContent()

    app = build_app()
    app.mount(ClashService())
    app.mount(GuardedService())

    with pytest.raises(StartupError) as caught:
        app.build()

    problems = caught.value.problems
    assert any("ItemOut" in p for p in problems)
    assert any("'admin'" in p for p in problems)


def test_a_route_on_the_openapi_path_is_a_startup_conflict() -> None:
    app = build_app()

    async def mine(c: CoreContext) -> Response:
        return c.text(200, "mine")

    app.get("/openapi.json", mine)

    with pytest.raises(StartupError) as caught:
        app.build()

    assert any("/openapi.json" in p for p in caught.value.problems)
