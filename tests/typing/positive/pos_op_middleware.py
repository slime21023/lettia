"""Per-operation and per-route middleware type-check through their public API."""

from typing import Protocol, override

from lettia import App, Context, NoContent, op, public
from lettia.core import Context as CoreContext
from lettia.core import Middleware, Response
from lettia.extensions.ratelimit import rate_limit


class AccountApi(Protocol):
    @op.post("/login", guard=public)
    async def login(self, c: Context[None], req: None) -> NoContent: ...

    @op.get("/me", guard=public)
    async def me(self, c: Context[None], req: None) -> NoContent: ...


class AccountService(AccountApi):
    @override
    async def login(self, c: Context[None], req: None) -> NoContent:
        return NoContent()

    @override
    async def me(self, c: Context[None], req: None) -> NoContent:
        return NoContent()


async def health(c: CoreContext) -> Response:
    return c.text(200, "ok")


def wire(app: App) -> None:
    limit: Middleware = rate_limit("5/minute")
    app.mount(AccountService(), middleware={AccountApi.login: [limit]})
    app.group("/v1").mount(
        AccountService(), middleware={AccountApi.login: [limit], AccountApi.me: []}
    )
    app.get("/health", health, middleware=[limit])
    app.group("/g", limit).post("/ping", health, middleware=(limit, limit))
