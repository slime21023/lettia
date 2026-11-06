"""Sessions and rate limiting type-check through their public API."""

from collections.abc import Mapping
from typing import assert_type

from lettia import ApiKeyCookie, App, GuardSpec
from lettia.core import Context, Middleware, RequestHead, Unauthorized
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


sessions = SessionMiddleware([b"k" * 32])
session_user = GuardSpec[str]("session", scheme=ApiKeyCookie("lettia_session"))


async def verify_session(head: RequestHead) -> str:
    data = sessions.read(head)
    assert_type(data, Mapping[str, SessionValue])
    user = data.get("user")
    if not isinstance(user, str):
        raise Unauthorized("sign in")
    return user


session_guard = session_user.use(verify_session)
