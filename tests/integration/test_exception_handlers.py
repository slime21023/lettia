"""Exception handlers on a contract app: responses, middleware and OpenAPI."""

from typing import Protocol, override

from lettia import App, Context, Ok, op, public
from lettia.core import Context as CoreContext
from lettia.core import Response
from lettia.middleware import request_id
from lettia.testing import TestClient


class Api(Protocol):
    @op.get("/users", guard=public)
    async def users(self, c: Context[None], req: None) -> Ok[str]: ...


class Service(Api):
    @override
    async def users(self, c: Context[None], req: None) -> Ok[str]:
        raise KeyError("no such user")


async def as_not_found(c: CoreContext, exc: KeyError) -> Response:
    return c.json(404, {"title": "Not Found", "detail": exc.args[0]})


def make_app(*, with_handler: bool) -> App:
    app = App()
    app.use(request_id())
    app.mount(Service())
    if with_handler:
        app.add_exception_handler(KeyError, as_not_found)
    return app


async def test_a_contract_operation_that_raises_is_answered_by_the_handler() -> None:
    async with TestClient(make_app(with_handler=True)) as http:
        res = await http.get("/users")

    assert res.status_code == 404
    assert res.json() == {"title": "Not Found", "detail": "no such user"}
    assert "x-request-id" in res.headers


async def test_without_a_handler_the_same_exception_is_a_500() -> None:
    async with TestClient(make_app(with_handler=False)) as http:
        res = await http.get("/users")

    assert res.status_code == 500


def test_a_registered_handler_does_not_change_the_openapi_document() -> None:
    assert (
        make_app(with_handler=True).openapi() == make_app(with_handler=False).openapi()
    )
