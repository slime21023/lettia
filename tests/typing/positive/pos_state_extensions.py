"""Sessions and rate limiting type-check through their public API."""

from typing import assert_type

from lettia import App
from lettia.core import Context, Middleware, RequestHead
from lettia.extensions.ratelimit import rate_limit
from lettia.extensions.sessions import (
    SESSION_KEY,
    Session,
    SessionMiddleware,
    SessionValue,
)


def use(c: Context) -> None:
    session = Session.of(c)
    assert_type(session, Session)
    assert_type(SESSION_KEY.get(c), Session)
    session["user"] = "ann"
    session["nested"] = {"items": [1, 2.5, True, None, "x"]}
    value = session.get("user", None)
    assert_type(value, SessionValue)
    assert_type(session.pop_flashes(), list[tuple[str, str]])
    session.flash("saved")
    session.flash("oops", "error")


def by_user(head: RequestHead) -> str | None:
    return head.headers.get("x-user")


def install(app: App) -> None:
    app.use(rate_limit("5/minute"))
    limiter: Middleware = rate_limit("10/second", key=by_user, max_keys=100)
    app.use(SessionMiddleware([b"k" * 32], same_site="strict", secure=True), limiter)
