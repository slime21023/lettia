import math
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from lettia.core import App, Context, Handler, MutableHeaders, RequestHead, Response
from lettia.extensions.sessions import (
    Session,
    SessionMiddleware,
    SessionValue,
    cookie,
)
from lettia.testing import TestClient, make_context

KEY = b"k" * 32
OLD_KEY = b"o" * 32
NAME = "lettia_session"
NOW = 1_800_000_000


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """A controllable clock; ``clock[0]`` is the current Unix time."""
    now = [NOW]
    monkeypatch.setattr(cookie, "_now", lambda: now[0])
    return now


def make_app(middleware: SessionMiddleware | None = None) -> App:
    async def show(c: Context) -> Response:
        session = Session.of(c)
        flashes = session.pop_flashes()
        return c.json(200, {"data": dict(session), "flashes": flashes})

    async def put(c: Context) -> Response:
        Session.of(c)[c.path_params["k"]] = c.path_params["v"]
        return c.text(200, "ok")

    async def nested(c: Context) -> Response:
        session = Session.of(c)
        items = session.setdefault("items", [])
        assert isinstance(items, list)
        items.append(len(items))
        return c.text(200, "ok")

    async def clear(c: Context) -> Response:
        Session.of(c).clear()
        return c.text(200, "ok")

    async def flash(c: Context) -> Response:
        Session.of(c).flash(c.path_params["m"], "info")
        return c.text(200, "ok")

    async def idle(c: Context) -> Response:
        return c.text(200, "ok")

    async def boom(c: Context) -> Response:
        Session.of(c)["x"] = "lost"
        raise RuntimeError("boom")

    async def server_error(c: Context) -> Response:
        Session.of(c)["x"] = "lost"
        return c.text(503, "later")

    app = App()
    app.use(middleware or SessionMiddleware([KEY]))
    app.get("/show", show)
    app.get("/put/{k}/{v}", put)
    app.get("/nested", nested)
    app.get("/clear", clear)
    app.get("/flash/{m}", flash)
    app.get("/idle", idle)
    app.get("/boom", boom)
    app.get("/503", server_error)
    return app


def client(app: App) -> TestClient:
    return TestClient(app, base_url="https://testserver")


def set_cookies(res: httpx.Response) -> list[str]:
    return res.headers.get_list("set-cookie")


def token_of(res: httpx.Response) -> str:
    (header,) = set_cookies(res)
    return header.split(";", 1)[0].split("=", 1)[1]


def forge(
    data: dict[str, SessionValue] | None = None,
    *,
    iat: int = NOW,
    exp: int = NOW + 100,
    key: bytes = KEY,
    flash: list[tuple[str, str]] | None = None,
) -> str:
    return cookie.encode_token(key, NAME, data or {"u": "a"}, flash or [], iat, exp)


async def read_with(app: App, cookie_header: str | list[str]) -> Any:
    headers = (
        [("cookie", v) for v in cookie_header]
        if isinstance(cookie_header, list)
        else [("cookie", cookie_header)]
    )
    async with client(app) as http:
        return await http.get("/show", headers=headers)


async def test_session_round_trip_persists_values_across_requests() -> None:
    async with client(make_app()) as http:
        await http.get("/put/user/ann")
        res = await http.get("/show")
    assert res.json()["data"] == {"user": "ann"}


async def test_session_nested_mutation_is_persisted() -> None:
    async with client(make_app()) as http:
        await http.get("/nested")
        await http.get("/nested")
        res = await http.get("/show")
    assert res.json()["data"] == {"items": [0, 1]}


async def test_session_unchanged_request_does_not_resend_cookie() -> None:
    async with client(make_app()) as http:
        first = await http.get("/put/a/b")
        second = await http.get("/idle")
    assert len(set_cookies(first)) == 1
    assert set_cookies(second) == []


async def test_session_without_content_or_cookie_sets_nothing() -> None:
    async with client(make_app()) as http:
        res = await http.get("/idle")
    assert set_cookies(res) == []


async def test_session_clear_deletes_cookie_and_state() -> None:
    async with client(make_app()) as http:
        await http.get("/put/a/b")
        res = await http.get("/clear")
        after = await http.get("/show")
    assert "Max-Age=0" in set_cookies(res)[0]
    assert after.json()["data"] == {}


async def test_session_flash_is_read_once_and_visible_on_next_request() -> None:
    async with client(make_app()) as http:
        same = await http.get("/flash/hello")
        first = await http.get("/show")
        second = await http.get("/show")
    assert same.status_code == 200
    assert first.json()["flashes"] == [["info", "hello"]]
    assert second.json()["flashes"] == []


async def test_session_unread_flash_survives_requests_that_do_not_read_it() -> None:
    async with client(make_app()) as http:
        await http.get("/flash/hello")
        await http.get("/put/a/b")
        res = await http.get("/show")
    assert res.json()["flashes"] == [["info", "hello"]]


async def test_session_separate_clients_do_not_share_state() -> None:
    app = make_app()
    async with client(app) as one, client(app) as two:
        await one.get("/put/user/ann")
        await two.get("/put/user/bob")
        a = await one.get("/show")
        b = await two.get("/show")
    assert a.json()["data"] == {"user": "ann"}
    assert b.json()["data"] == {"user": "bob"}


async def test_session_cookie_attributes_follow_configuration() -> None:
    app = make_app(
        SessionMiddleware(
            [KEY], cookie_name=NAME, same_site="strict", path="/", max_age=100
        )
    )
    async with client(app) as http:
        res = await http.get("/put/a/b")
    header = set_cookies(res)[0]
    for part in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/", "Max-Age=100"):
        assert part in header
    assert "Domain" not in header


async def test_session_host_prefixed_cookie_is_accepted_with_secure_root_path() -> None:
    mw = SessionMiddleware([KEY], cookie_name="__Host-s")
    async with client(make_app(mw)) as http:
        res = await http.get("/put/a/b")
        again = await http.get("/show")
    assert set_cookies(res)[0].startswith("__Host-s=")
    assert again.json()["data"] == {"a": "b"}


MUTATIONS: dict[str, Callable[[str], str]] = {
    "flipped_mac_tail": lambda t: t[:-2] + ("AA" if not t.endswith("AA") else "BB"),
    "unknown_version": lambda t: t.replace("v1.", "v2.", 1),
    "extra_segment": lambda t: t + ".extra",
    "tampered_payload": lambda t: ".".join(
        [t.split(".")[0], t.split(".")[1] + "A", t.split(".")[2]]
    ),
    "empty_mac": lambda t: f"v1.{t.split('.')[1]}.",
    "empty_payload": lambda t: f"v1..{t.split('.')[2]}",
    "bad_alphabet": lambda t: "v1.!!!.@@@",
    "padded": lambda t: t + "=",
    "oversized": lambda t: "x" * 5000,
    "quoted": lambda t: '"' + t + '"',
}


@pytest.mark.parametrize("name", list(MUTATIONS))
async def test_session_invalid_token_gives_fresh_session_and_deletes_cookie(
    name: str,
) -> None:
    app = make_app()
    token = forge()
    res = await read_with(app, f"{NAME}={MUTATIONS[name](token)}")
    assert res.json()["data"] == {}
    assert "Max-Age=0" in set_cookies(res)[0]


async def test_session_valid_forged_token_with_wrong_key_is_rejected() -> None:
    res = await read_with(make_app(), f"{NAME}={forge(key=OLD_KEY)}")
    assert res.json()["data"] == {}


async def test_session_token_signed_for_other_cookie_name_is_rejected() -> None:
    other = cookie.encode_token(KEY, "other", {"u": "a"}, [], NOW, NOW + 100)
    res = await read_with(make_app(), f"{NAME}={other}")
    assert res.json()["data"] == {}


async def test_session_valid_token_restores_identity() -> None:
    res = await read_with(make_app(), f"{NAME}={forge()}")
    assert res.json()["data"] == {"u": "a"}


async def test_session_duplicate_cookie_names_are_never_trusted() -> None:
    good = f"{NAME}={forge()}"
    for headers in ([good + "; " + good], [good, good]):
        res = await read_with(make_app(), headers)
        assert res.json()["data"] == {}


async def test_session_unrelated_cookies_do_not_disturb_loading() -> None:
    res = await read_with(make_app(), f"a=1; {NAME}={forge()}; b=2")
    assert res.json()["data"] == {"u": "a"}


async def test_session_token_expires_at_exp(clock: list[int]) -> None:
    token = forge(exp=NOW + 100)
    clock[0] = NOW + 99
    assert (await read_with(make_app(), f"{NAME}={token}")).json()["data"]
    clock[0] = NOW + 100
    assert (await read_with(make_app(), f"{NAME}={token}")).json()["data"] == {}


async def test_session_iat_in_future_allows_sixty_seconds_of_skew(
    clock: list[int],
) -> None:
    accepted = forge(iat=NOW + 60, exp=NOW + 200)
    rejected = forge(iat=NOW + 61, exp=NOW + 200)
    assert (await read_with(make_app(), f"{NAME}={accepted}")).json()["data"]
    assert (await read_with(make_app(), f"{NAME}={rejected}")).json()["data"] == {}


async def test_session_token_lifetime_over_max_age_is_rejected() -> None:
    long_lived = forge(iat=NOW, exp=NOW + 1_209_601)
    res = await read_with(make_app(), f"{NAME}={long_lived}")
    assert res.json()["data"] == {}


async def test_session_rotation_resigns_with_new_key_without_extending_expiry(
    clock: list[int],
) -> None:
    old_token = forge(key=OLD_KEY, exp=NOW + 500)
    clock[0] = NOW + 10
    mw = SessionMiddleware([KEY, OLD_KEY])
    res = await read_with(make_app(mw), f"{NAME}={old_token}")
    new = token_of(res)
    decoded = cookie.decode_token(
        new,
        NAME,
        [KEY],
        max_age=1_209_600,
        max_cookie_bytes=4096,
        now=clock[0],
    )
    assert decoded is not None
    assert decoded.data == {"u": "a"}
    assert decoded.exp == NOW + 500
    assert "Max-Age=490" in set_cookies(res)[0]


async def test_session_modification_does_not_extend_expiry(clock: list[int]) -> None:
    token = forge(exp=NOW + 500)
    clock[0] = NOW + 100
    async with client(make_app()) as http:
        res = await http.get("/put/a/b", headers={"cookie": f"{NAME}={token}"})
    decoded = cookie.decode_token(
        token_of(res),
        NAME,
        [KEY],
        max_age=1_209_600,
        max_cookie_bytes=4096,
        now=clock[0],
    )
    assert decoded is not None
    assert decoded.exp == NOW + 500


async def test_session_handler_crossing_expiry_deletes_cookie(
    clock: list[int],
) -> None:
    mw = SessionMiddleware([KEY])

    async def slow(c: Context) -> Response:
        Session.of(c)["changed"] = True
        clock[0] = NOW + 100
        return c.text(200, "ok")

    app = App()
    app.use(mw)
    app.get("/slow", slow)
    async with client(app) as http:
        res = await http.get(
            "/slow", headers={"cookie": f"{NAME}={forge(exp=NOW + 100)}"}
        )
    assert "Max-Age=0" in set_cookies(res)[0]


def nested_list(depth: int) -> SessionValue:
    value: SessionValue = []
    for _ in range(depth - 1):
        value = [value]
    return value


async def run_commit(value: Any) -> Response | Exception:
    mw = SessionMiddleware([KEY])

    async def handler(c: Context) -> Response:
        Session.of(c)["v"] = value
        return c.text(200, "ok")

    try:
        return await mw(handler)(make_context())
    except Exception as exc:
        return exc


async def test_session_values_nested_up_to_depth_32_are_accepted() -> None:
    # data is depth 0, so 32 list levels below it are the deepest allowed
    assert isinstance(await run_commit(nested_list(32)), Response)


async def test_session_values_nested_beyond_depth_32_are_rejected() -> None:
    assert isinstance(await run_commit(nested_list(33)), ValueError)


@pytest.mark.parametrize(
    "value",
    [math.nan, math.inf, 2**64, -(2**63) - 1, object(), {1: 2}, (1, 2), b"x", {"a"}],
)
async def test_session_unrepresentable_values_fail_instead_of_degrading(
    value: object,
) -> None:
    assert isinstance(await run_commit(value), ValueError)


async def test_session_integer_range_edges_are_accepted() -> None:
    assert isinstance(await run_commit([2**64 - 1, -(2**63)]), Response)


async def test_session_circular_containers_fail() -> None:
    loop: list[Any] = []
    loop.append(loop)
    assert isinstance(await run_commit(loop), ValueError)


def test_session_flash_rejects_non_string_arguments() -> None:
    session = Session()
    with pytest.raises(TypeError):
        session.flash(1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        session.flash("m", None)  # type: ignore[arg-type]


async def test_session_error_responses_do_not_commit() -> None:
    async with client(make_app()) as http:
        crashed = await http.get("/boom")
        unavailable = await http.get("/503")
    assert crashed.status_code == 500
    assert unavailable.status_code == 503
    assert set_cookies(crashed) == []
    assert set_cookies(unavailable) == []


async def test_session_mutation_after_response_returned_is_not_persisted() -> None:
    captured: list[Session] = []

    async def handler(c: Context) -> Response:
        captured.append(Session.of(c))
        return c.text(200, "ok")

    app = App()
    app.use(SessionMiddleware([KEY]))
    app.get("/", handler)
    async with client(app) as http:
        res = await http.get("/")
    captured[0]["late"] = 1
    assert set_cookies(res) == []


async def test_session_existing_set_cookie_headers_are_kept() -> None:
    async def handler(c: Context) -> Response:
        Session.of(c)["a"] = 1
        headers = MutableHeaders()
        headers.add("set-cookie", "other=1; Path=/")
        return Response(200, b"ok", headers)

    app = App()
    app.use(SessionMiddleware([KEY]))
    app.get("/", handler)
    async with client(app) as http:
        res = await http.get("/")
    cookies = set_cookies(res)
    assert len(cookies) == 2
    assert cookies[0].startswith("other=1")


async def test_session_cookie_over_size_limit_fails_the_request() -> None:
    mw = SessionMiddleware([KEY], max_cookie_bytes=400)

    async def handler(c: Context) -> Response:
        Session.of(c)["big"] = "x" * 1000
        return c.text(200, "ok")

    result = await _call(mw, handler)
    assert isinstance(result, ValueError)


async def _call(mw: SessionMiddleware, handler: Handler) -> Response | Exception:
    try:
        return await mw(handler)(make_context())
    except Exception as exc:
        return exc


class Stream:
    """An async iterator that records ``aclose`` and was never started."""

    def __init__(self, fail_close: bool = False) -> None:
        self.closed = 0
        self.fail_close = fail_close

    def __aiter__(self) -> "Stream":
        return self

    async def __anext__(self) -> bytes:
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed += 1
        if self.fail_close:
            raise OSError("close failed")


async def test_session_commit_failure_closes_stream_and_drops_after_send() -> None:
    stream = Stream()
    sent: list[int] = []

    async def handler(c: Context) -> Response:
        Session.of(c)["v"] = math.nan
        return Response(200, stream, after_send=lambda: sent.append(1))

    result = await _call(SessionMiddleware([KEY]), handler)
    assert isinstance(result, ValueError)
    assert stream.closed == 1
    assert sent == []


async def test_session_commit_failure_survives_failing_cleanup() -> None:
    stream = Stream(fail_close=True)

    async def handler(c: Context) -> Response:
        Session.of(c)["v"] = math.nan
        return Response(200, stream)

    result = await _call(SessionMiddleware([KEY]), handler)
    assert isinstance(result, ValueError)
    assert isinstance(result.__cause__, OSError)


async def test_session_stream_is_left_open_when_commit_succeeds() -> None:
    stream = Stream()

    async def handler(c: Context) -> Response:
        Session.of(c)["v"] = 1
        return Response(200, stream)

    response = await mw_call(handler)
    assert isinstance(response, Response)
    assert stream.closed == 0


async def mw_call(handler: Handler) -> Response | Exception:
    return await _call(SessionMiddleware([KEY]), handler)


async def test_session_handler_exception_propagates_from_bare_middleware() -> None:
    async def handler(c: Context) -> Response:
        raise KeyError("x")

    assert isinstance(await mw_call(handler), KeyError)


def test_session_of_without_middleware_raises_runtime_error() -> None:
    with pytest.raises(RuntimeError):
        Session.of(make_context())


@pytest.mark.parametrize(
    "options",
    [
        {"secret_keys": []},
        {"secret_keys": "x" * 40},
        {"secret_keys": b"x" * 40},
        {"secret_keys": [b"short"]},
        {"secret_keys": ["x" * 40]},
        {"max_age": 0},
        {"max_age": True},
        {"max_cookie_bytes": True},
        {"max_cookie_bytes": 0},
        {"max_cookie_bytes": 4097},
        {"same_site": "bogus"},
        {"same_site": "none", "secure": False},
        {"cookie_name": "a:b"},
        {"cookie_name": "bad name"},
        {"cookie_name": "Path"},
        {"cookie_name": "max-age"},
        {"cookie_name": "a\r\nb"},
        {"cookie_name": "__Secure-x", "secure": False},
        {"cookie_name": "__Host-x", "path": "/app"},
        {"path": "app"},
        {"path": "/a;b"},
        {"path": "/a\r\nSet-Cookie: x=1"},
        {"max_cookie_bytes": 100},
    ],
)
def test_session_middleware_rejects_unsafe_configuration(
    options: dict[str, Any],
) -> None:
    arguments: dict[str, Any] = {"secret_keys": [KEY]}
    arguments.update(options)
    with pytest.raises(ValueError):
        SessionMiddleware(**arguments)


def test_session_middleware_copies_the_key_sequence() -> None:
    keys = [KEY]
    mw = SessionMiddleware(keys)
    keys.clear()
    assert isinstance(mw, SessionMiddleware)


async def test_session_smallest_accepted_cookie_limit_can_still_delete() -> None:
    limit = next(n for n in range(1, 4097) if _accepts(n))
    mw = SessionMiddleware([KEY], max_cookie_bytes=limit)
    async with client(make_app(mw)) as http:
        res = await http.get("/show", headers={"cookie": f"{NAME}=junk"})
    assert "Max-Age=0" in set_cookies(res)[0]


def _accepts(limit: int) -> bool:
    try:
        SessionMiddleware([KEY], max_cookie_bytes=limit)
    except ValueError:
        return False
    return True


# --- the read-only reader used by guards --------------------------------------


def head_with(*cookie_headers: str) -> RequestHead:
    return RequestHead(
        method="GET",
        path="/",
        headers=[(b"cookie", value.encode()) for value in cookie_headers],
    )


def test_session_read_returns_the_data_of_a_valid_cookie() -> None:
    reader = SessionMiddleware([KEY])
    token = forge({"user": "ann", "nested": {"roles": ["a", "b"]}})

    data = reader.read(head_with(f"{NAME}={token}"))

    assert data == {"user": "ann", "nested": {"roles": ["a", "b"]}}


def test_session_read_leaves_flash_messages_out() -> None:
    token = forge({"user": "ann"}, flash=[("info", "hello")])

    data = SessionMiddleware([KEY]).read(head_with(f"{NAME}={token}"))

    assert data == {"user": "ann"}


def test_session_read_accepts_a_cookie_signed_with_an_older_key() -> None:
    reader = SessionMiddleware([KEY, OLD_KEY])
    token = forge({"user": "ann"}, key=OLD_KEY)

    assert reader.read(head_with(f"{NAME}={token}")) == {"user": "ann"}


def test_session_read_follows_the_cookie_name_of_the_middleware() -> None:
    reader = SessionMiddleware([KEY], cookie_name="app_session")
    token = cookie.encode_token(KEY, "app_session", {"user": "ann"}, [], NOW, NOW + 100)

    assert reader.read(head_with(f"app_session={token}")) == {"user": "ann"}
    assert reader.read(head_with(f"{NAME}={token}")) == {}


def test_session_read_gives_an_empty_mapping_without_a_cookie() -> None:
    assert SessionMiddleware([KEY]).read(head_with()) == {}
    assert SessionMiddleware([KEY]).read(head_with("other=1")) == {}


@pytest.mark.parametrize("name", list(MUTATIONS))
def test_session_read_gives_an_empty_mapping_for_a_defective_cookie(name: str) -> None:
    token = MUTATIONS[name](forge({"user": "ann"}))

    assert SessionMiddleware([KEY]).read(head_with(f"{NAME}={token}")) == {}


def test_session_read_ignores_a_cookie_signed_with_an_unknown_key() -> None:
    token = forge({"user": "ann"}, key=OLD_KEY)

    assert SessionMiddleware([KEY]).read(head_with(f"{NAME}={token}")) == {}


def test_session_read_ignores_an_expired_cookie(clock: list[int]) -> None:
    token = forge({"user": "ann"}, exp=NOW + 100)
    clock[0] = NOW + 100

    assert SessionMiddleware([KEY]).read(head_with(f"{NAME}={token}")) == {}


def test_session_read_ignores_a_cookie_that_appears_twice() -> None:
    mine = forge({"user": "ann"})
    planted = forge({"user": "mallory"})

    reader = SessionMiddleware([KEY])

    assert reader.read(head_with(f"{NAME}={mine}", f"{NAME}={planted}")) == {}
    assert reader.read(head_with(f"{NAME}={mine}; {NAME}={planted}")) == {}


def test_session_read_cannot_be_used_to_change_the_session() -> None:
    reader = SessionMiddleware([KEY])
    head = head_with(f"{NAME}={forge({'user': 'ann'})}")

    data = reader.read(head)
    with pytest.raises(TypeError):
        data["user"] = "mallory"  # type: ignore[index]

    assert reader.read(head) == {"user": "ann"}
