"""The ready-made response classes for 202, 400, 409 and redirects, end to end."""

from typing import Any, Protocol, override

import msgspec
import pytest

from lettia import (
    Accepted,
    App,
    BadRequest,
    Conflict,
    Context,
    Found,
    MovedPermanently,
    Ok,
    PermanentRedirect,
    SeeOther,
    TemporaryRedirect,
    op,
    public,
)
from lettia.testing import TestClient


class Job(msgspec.Struct):
    id: int


class Reason(msgspec.Struct):
    message: str


class Api(Protocol):
    @op.post("/jobs", guard=public)
    async def create(
        self, c: Context[None], req: None
    ) -> Accepted[Job] | BadRequest[Reason] | Conflict[Reason] | Ok[Job]: ...

    @op.get("/old", guard=public)
    async def old(self, c: Context[None], req: None) -> MovedPermanently: ...

    @op.get("/found", guard=public)
    async def found(self, c: Context[None], req: None) -> Found | Ok[str]: ...

    @op.post("/after", guard=public)
    async def after(self, c: Context[None], req: None) -> SeeOther: ...

    @op.get("/temp", guard=public)
    async def temp(self, c: Context[None], req: None) -> TemporaryRedirect: ...

    @op.get("/perm", guard=public)
    async def perm(self, c: Context[None], req: None) -> PermanentRedirect: ...


class Service(Api):
    def __init__(self) -> None:
        self.outcome = "accepted"

    @override
    async def create(
        self, c: Context[None], req: None
    ) -> Accepted[Job] | BadRequest[Reason] | Conflict[Reason] | Ok[Job]:
        if self.outcome == "bad":
            return BadRequest(Reason("no"))
        if self.outcome == "conflict":
            return Conflict(Reason("exists"))
        return Accepted(Job(7))

    @override
    async def old(self, c: Context[None], req: None) -> MovedPermanently:
        return MovedPermanently("/new")

    @override
    async def found(self, c: Context[None], req: None) -> Found | Ok[str]:
        return Found("/login?next=%2Fhome")

    @override
    async def after(self, c: Context[None], req: None) -> SeeOther:
        return SeeOther("/jobs/7")

    @override
    async def temp(self, c: Context[None], req: None) -> TemporaryRedirect:
        return TemporaryRedirect("/maintenance")

    @override
    async def perm(self, c: Context[None], req: None) -> PermanentRedirect:
        return PermanentRedirect("/v2/perm")


def make_app() -> tuple[App, Service]:
    service = Service()
    app = App()
    app.mount(service)
    return app, service


@pytest.mark.parametrize(
    ("outcome", "status", "body"),
    [
        ("accepted", 202, {"id": 7}),
        ("bad", 400, {"message": "no"}),
        ("conflict", 409, {"message": "exists"}),
    ],
)
async def test_status_reply_classes_send_their_status_and_json_body(
    outcome: str, status: int, body: dict[str, Any]
) -> None:
    app, service = make_app()
    service.outcome = outcome
    async with TestClient(app) as http:
        res = await http.post("/jobs")

    assert res.status_code == status
    assert res.headers["content-type"] == "application/json"
    assert res.json() == body


@pytest.mark.parametrize(
    ("method", "path", "status", "location"),
    [
        ("GET", "/old", 301, "/new"),
        ("GET", "/found", 302, "/login?next=%2Fhome"),
        ("POST", "/after", 303, "/jobs/7"),
        ("GET", "/temp", 307, "/maintenance"),
        ("GET", "/perm", 308, "/v2/perm"),
    ],
)
async def test_redirect_reply_classes_send_status_and_location_without_a_body(
    method: str, path: str, status: int, location: str
) -> None:
    app, _ = make_app()
    async with TestClient(app) as http:
        res = await http.request(method, path)

    assert res.status_code == status
    assert res.headers["location"] == location
    assert res.content == b""


async def test_a_redirect_without_its_location_fails_instead_of_sending_one() -> None:
    class Missing(Service):
        @override
        async def found(self, c: Context[None], req: None) -> Found | Ok[str]:
            return Found(None)  # type: ignore[arg-type]

    app = App()
    app.mount(Missing())
    async with TestClient(app) as http:
        res = await http.get("/found")

    assert res.status_code == 500


def test_openapi_describes_the_new_statuses_and_the_required_location() -> None:
    app, _ = make_app()
    paths = app.openapi()["paths"]

    jobs = paths["/jobs"]["post"]["responses"]
    assert {k: jobs[k]["description"] for k in ("202", "400", "409")} == {
        "202": "Accepted",
        "400": "Bad Request",
        "409": "Conflict",
    }
    for status, path, reason in [
        ("301", "/old", "Moved Permanently"),
        ("302", "/found", "Found"),
        ("303", "/after", "See Other"),
        ("307", "/temp", "Temporary Redirect"),
        ("308", "/perm", "Permanent Redirect"),
    ]:
        [operation] = paths[path].values()
        response = operation["responses"][status]
        assert response["description"] == reason
        assert response["headers"]["Location"]["required"] is True
        assert "content" not in response
