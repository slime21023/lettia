"""The ready-made replies, redirects and cookie helpers type-check."""

from datetime import UTC, datetime
from typing import Protocol, override

import msgspec

from lettia import (
    Accepted,
    BadRequest,
    Conflict,
    Context,
    Found,
    MovedPermanently,
    PermanentRedirect,
    SeeOther,
    TemporaryRedirect,
    op,
    public,
)
from lettia.core import Context as CoreContext
from lettia.core import Response


class Job(msgspec.Struct):
    id: int


class Reason(msgspec.Struct):
    message: str


class JobsApi(Protocol):
    @op.post("/jobs", guard=public)
    async def create(
        self, c: Context[None], req: None
    ) -> Accepted[Job] | BadRequest[Reason] | Conflict[Reason]: ...

    @op.get("/go", guard=public)
    async def go(
        self, c: Context[None], req: None
    ) -> (
        MovedPermanently | Found | SeeOther | TemporaryRedirect | PermanentRedirect
    ): ...


class JobsService(JobsApi):
    @override
    async def create(
        self, c: Context[None], req: None
    ) -> Accepted[Job] | BadRequest[Reason] | Conflict[Reason]:
        return Accepted(Job(1))

    @override
    async def go(
        self, c: Context[None], req: None
    ) -> MovedPermanently | Found | SeeOther | TemporaryRedirect | PermanentRedirect:
        return Found(location="/jobs")


async def core_handler(c: CoreContext) -> Response:
    response = c.redirect("/login", 303)
    response.set_cookie(
        "sid",
        "abc",
        max_age=60,
        expires=datetime(2030, 1, 1, tzinfo=UTC),
        secure=True,
        samesite="strict",
    )
    response.delete_cookie("old", path="/app", samesite=None)
    return response
