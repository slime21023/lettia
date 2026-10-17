import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import httpx
import pytest

from lettia.core import (
    App,
    ClientDisconnect,
    Context,
    Forbidden,
    Handler,
    HTTPError,
    Response,
    StartupError,
    Unauthorized,
)

Message = MutableMapping[str, Any]


def client(app: App) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def text(body: str, status: int = 200) -> Handler:
    async def handler(c: Context) -> Response:
        return c.text(status, body)

    return handler


async def echo_params(c: Context) -> Response:
    return c.json(200, c.path_params)


# --- routing ------------------------------------------------------------


async def test_app_routes_a_get_request_to_its_handler() -> None:
    app = App()
    app.get("/hello", text("hi"))

    async with client(app) as http:
        res = await http.get("/hello")

    assert (res.status_code, res.text) == (200, "hi")


@pytest.mark.parametrize("verb", ["get", "post", "put", "patch", "delete", "options"])
async def test_app_registers_a_route_for_every_http_verb(verb: str) -> None:
    app = App()
    getattr(app, verb)("/r", text(verb))

    async with client(app) as http:
        res = await http.request(verb.upper(), "/r")

    assert res.text == verb


async def test_app_route_accepts_any_method_by_name() -> None:
    app = App()
    app.route("PROPFIND", "/r", text("dav"))

    async with client(app) as http:
        res = await http.request("PROPFIND", "/r")

    assert res.text == "dav"


async def test_app_head_is_answered_by_the_get_route_without_a_body() -> None:
    app = App()
    app.get("/hello", text("hi"))

    async with client(app) as http:
        res = await http.head("/hello")

    assert res.status_code == 200
    assert res.content == b""
    assert res.headers["content-length"] == "2"


async def test_app_passes_path_parameters_to_the_handler() -> None:
    app = App()
    app.get("/users/{id}/posts/{post}", echo_params)

    async with client(app) as http:
        res = await http.get("/users/7/posts/a%20b")

    assert res.json() == {"id": "7", "post": "a b"}


async def test_app_unknown_path_is_a_404_problem() -> None:
    app = App()
    app.get("/hello", text("hi"))

    async with client(app) as http:
        res = await http.get("/missing")

    assert res.status_code == 404
    assert res.headers["content-type"] == "application/problem+json"
    assert res.json() == {"type": "about:blank", "title": "Not Found", "status": 404}


async def test_app_wrong_method_is_a_405_with_an_allow_header() -> None:
    app = App()
    app.get("/hello", text("hi"))
    app.post("/hello", text("posted"))

    async with client(app) as http:
        res = await http.delete("/hello")

    assert res.status_code == 405
    assert res.headers["allow"] == "GET, HEAD, POST"
    assert res.json()["title"] == "Method Not Allowed"


async def test_app_builds_itself_on_the_first_request() -> None:
    app = App()
    app.get("/a", text("a"))

    async with client(app) as http:
        assert (await http.get("/a")).status_code == 200
        assert (await http.get("/a")).status_code == 200


def test_app_cannot_register_routes_after_it_is_built() -> None:
    app = App()
    app.get("/a", text("a"))
    app.build()

    with pytest.raises(RuntimeError, match="built"):
        app.get("/b", text("b"))


def test_app_cannot_add_middleware_or_hooks_after_it_is_built() -> None:
    app = App()
    app.build()

    with pytest.raises(RuntimeError, match="built"):
        app.use(lambda next_: next_)
    with pytest.raises(RuntimeError, match="built"):
        app.group("/g")
    with pytest.raises(RuntimeError, match="built"):
        app.on_startup(lambda: _noop())


async def _noop() -> None:
    return None


def test_app_build_reports_every_route_problem_at_once() -> None:
    app = App()
    app.get("/users/{id}", text("a"))
    app.get("/users/{uid}", text("b"))
    app.get("no-slash", text("c"))
    app.get("/a/*/b", text("d"))

    with pytest.raises(StartupError) as caught:
        app.build()

    problems = caught.value.problems
    assert len(problems) == 3
    assert any("/users/{uid}" in p for p in problems)
    assert any("no-slash" in p for p in problems)
    assert any("/a/*/b" in p for p in problems)
    assert "no-slash" in str(caught.value)


def test_app_build_is_idempotent() -> None:
    app = App()
    app.get("/a", text("a"))

    app.build()
    app.build()


# --- groups -------------------------------------------------------------


async def test_group_prefixes_its_routes() -> None:
    app = App()
    app.group("/v1").get("/users", text("users"))

    async with client(app) as http:
        assert (await http.get("/v1/users")).text == "users"
        assert (await http.get("/users")).status_code == 404


async def test_group_nested_groups_concatenate_prefixes() -> None:
    app = App()
    app.group("/v1").group("/admin").get("/stats", text("stats"))

    async with client(app) as http:
        assert (await http.get("/v1/admin/stats")).text == "stats"


async def test_group_supports_a_root_path_and_a_wildcard() -> None:
    app = App()
    static = app.group("/static")
    static.get("/", text("index"))
    static.get("/*", echo_params)

    async with client(app) as http:
        assert (await http.get("/static/")).text == "index"
        assert (await http.get("/static/css/a.css")).json() == {"*": "css/a.css"}


@pytest.mark.parametrize("prefix", ["v1", "/v1/", "/"])
def test_group_prefix_must_start_with_a_slash_and_not_end_with_one(prefix: str) -> None:
    with pytest.raises(ValueError, match="prefix"):
        App().group(prefix)


# --- middleware ---------------------------------------------------------


def tracer(name: str, trace: list[str]) -> Callable[[Handler], Handler]:
    def middleware(next_: Handler) -> Handler:
        async def run(c: Context) -> Response:
            trace.append(f"{name}>")
            response = await next_(c)
            trace.append(f"<{name}")
            return response

        return run

    return middleware


async def test_middleware_wraps_the_handler_outermost_first() -> None:
    trace: list[str] = []

    async def handler(c: Context) -> Response:
        trace.append("handler")
        return c.text(200, "ok")

    app = App()
    app.use(tracer("a", trace), tracer("b", trace))
    app.get("/x", handler)

    async with client(app) as http:
        await http.get("/x")

    assert trace == ["a>", "b>", "handler", "<b", "<a"]


async def test_middleware_use_calls_accumulate_in_order() -> None:
    trace: list[str] = []
    app = App()
    app.use(tracer("a", trace))
    app.use(tracer("b", trace))
    app.get("/x", text("ok"))

    async with client(app) as http:
        await http.get("/x")

    assert trace == ["a>", "b>", "<b", "<a"]


async def test_middleware_registered_after_routes_still_applies() -> None:
    trace: list[str] = []
    app = App()
    app.get("/x", text("ok"))
    app.use(tracer("late", trace))

    async with client(app) as http:
        await http.get("/x")

    assert trace == ["late>", "<late"]


async def test_group_middleware_runs_inside_app_middleware_for_group_routes_only() -> (
    None
):
    trace: list[str] = []
    app = App()
    app.use(tracer("app", trace))
    app.group("/g", tracer("group", trace)).get("/x", text("in"))
    app.get("/y", text("out"))

    async with client(app) as http:
        await http.get("/g/x")
        assert trace == ["app>", "group>", "<group", "<app"]
        trace.clear()
        await http.get("/y")

    assert trace == ["app>", "<app"]


async def test_app_middleware_wraps_404_and_405_but_group_middleware_does_not() -> None:
    trace: list[str] = []
    app = App()
    app.use(tracer("app", trace))
    app.group("/g", tracer("group", trace)).get("/x", text("in"))

    async with client(app) as http:
        assert (await http.get("/nothing")).status_code == 404
        assert (await http.post("/g/x")).status_code == 405

    assert trace == ["app>", "<app", "app>", "<app"]


async def test_middleware_can_add_headers_to_the_response() -> None:
    def tag(next_: Handler) -> Handler:
        async def run(c: Context) -> Response:
            response = await next_(c)
            response.headers.set("x-tag", "yes")
            return response

        return run

    app = App()
    app.use(tag)
    app.get("/x", text("ok"))

    async with client(app) as http:
        assert (await http.get("/x")).headers["x-tag"] == "yes"
        assert (await http.get("/missing")).headers["x-tag"] == "yes"


async def test_middleware_runs_after_routing_and_sees_path_parameters() -> None:
    seen: list[dict[str, str]] = []

    def spy(next_: Handler) -> Handler:
        async def run(c: Context) -> Response:
            seen.append(dict(c.path_params))
            return await next_(c)

        return run

    app = App()
    app.use(spy)
    app.get("/users/{id}", echo_params)

    async with client(app) as http:
        await http.get("/users/9")

    assert seen == [{"id": "9"}]


async def test_middleware_receives_error_responses_instead_of_exceptions() -> None:
    statuses: list[int] = []

    def observe(next_: Handler) -> Handler:
        async def run(c: Context) -> Response:
            response = await next_(c)
            statuses.append(response.status)
            return response

        return run

    async def denied(c: Context) -> Response:
        raise Forbidden("nope")

    app = App()
    app.use(observe)
    app.get("/denied", denied)

    async with client(app) as http:
        await http.get("/denied")
        await http.get("/missing")

    assert statuses == [403, 404]


async def test_an_exception_raised_by_middleware_itself_becomes_a_500() -> None:
    def broken(next_: Handler) -> Handler:
        async def run(c: Context) -> Response:
            raise RuntimeError("middleware bug")

        return run

    app = App()
    app.use(broken)
    app.get("/x", text("ok"))

    async with client(app) as http:
        res = await http.get("/x")

    assert res.status_code == 500


# --- error handling -----------------------------------------------------


async def test_http_error_becomes_problem_json_with_its_detail() -> None:
    async def handler(c: Context) -> Response:
        raise HTTPError(422, "Unprocessable Entity", "name is required")

    app = App()
    app.get("/x", handler)

    async with client(app) as http:
        res = await http.get("/x")

    assert res.status_code == 422
    assert res.headers["content-type"] == "application/problem+json"
    assert res.json() == {
        "type": "about:blank",
        "title": "Unprocessable Entity",
        "status": 422,
        "detail": "name is required",
    }


async def test_http_error_headers_are_sent_with_the_response() -> None:
    async def handler(c: Context) -> Response:
        raise HTTPError(429, "Too Many Requests", headers={"retry-after": "30"})

    app = App()
    app.get("/x", handler)

    async with client(app) as http:
        res = await http.get("/x")

    assert res.headers["retry-after"] == "30"


@pytest.mark.parametrize(
    ("error", "status"), [(Unauthorized(), 401), (Forbidden(), 403)]
)
async def test_guard_errors_map_to_their_status(error: HTTPError, status: int) -> None:
    async def handler(c: Context) -> Response:
        raise error

    app = App()
    app.get("/x", handler)

    async with client(app) as http:
        assert (await http.get("/x")).status_code == status


async def test_unexpected_exception_becomes_a_500_without_leaking_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handler(c: Context) -> Response:
        raise ValueError("secret internals")

    app = App()
    app.get("/x", handler)

    with caplog.at_level(logging.ERROR, logger="lettia"):
        async with client(app) as http:
            res = await http.get("/x")

    assert res.status_code == 500
    assert res.json() == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
    }
    assert "secret internals" not in res.text
    assert any("secret internals" in str(r.exc_info) for r in caplog.records)


async def test_debug_mode_adds_an_exception_summary_to_the_detail() -> None:
    async def handler(c: Context) -> Response:
        raise ValueError("boom")

    app = App(debug=True)
    app.get("/x", handler)

    async with client(app) as http:
        res = await http.get("/x")

    assert res.status_code == 500
    assert res.json()["detail"] == "ValueError: boom"


async def test_oversized_body_is_a_413_problem() -> None:
    async def read(c: Context) -> Response:
        return c.bytes(200, await c.request.body())

    app = App(max_body_size=10)
    app.post("/x", read)

    async with client(app) as http:
        ok = await http.post("/x", content=b"x" * 10)
        too_big = await http.post("/x", content=b"x" * 11)

    assert ok.status_code == 200
    assert too_big.status_code == 413
    assert too_big.json()["title"] == "Payload Too Large"


async def test_custom_error_handler_receives_the_context_and_the_exception() -> None:
    seen: list[tuple[str, type[Exception]]] = []

    async def handle(c: Context, exc: Exception) -> Response:
        seen.append((c.request.path, type(exc)))
        return c.text(418, "custom")

    async def boom(c: Context) -> Response:
        raise ValueError("x")

    app = App(error_handler=handle)
    app.get("/boom", boom)

    async with client(app) as http:
        assert (await http.get("/boom")).status_code == 418
        assert (await http.get("/missing")).text == "custom"

    assert seen == [("/boom", ValueError), ("/missing", HTTPError)]


async def test_failing_error_handler_falls_back_to_a_plain_500() -> None:
    async def handle(c: Context, exc: Exception) -> Response:
        raise RuntimeError("handler bug")

    app = App(error_handler=handle)

    async with client(app) as http:
        res = await http.get("/missing")

    assert res.status_code == 500
    assert res.json()["title"] == "Internal Server Error"


async def test_client_disconnect_is_not_converted_into_a_response() -> None:
    async def handler(c: Context) -> Response:
        raise ClientDisconnect

    app = App()
    app.post("/x", handler)
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "raw_path": b"/x",
        "query_string": b"",
        "headers": [],
    }
    await app(scope, receive, send)

    assert sent == []


# --- context class ------------------------------------------------------


async def test_app_creates_contexts_with_the_configured_class() -> None:
    class AuditContext(Context):
        marker = "audit"

    async def handler(c: Context) -> Response:
        return c.text(200, getattr(c, "marker", "plain"))

    app = App(context_class=AuditContext)
    app.get("/x", handler)

    async with client(app) as http:
        assert (await http.get("/x")).text == "audit"


# --- lifespan -----------------------------------------------------------


async def run_lifespan(app: App, *events: str) -> list[Message]:
    queue: list[Message] = [{"type": e} for e in events]
    sent: list[Message] = []

    async def receive() -> Message:
        return queue.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    await app({"type": "lifespan"}, receive, send)
    return sent


def recorder(name: str, log: list[str]) -> Callable[[], Awaitable[None]]:
    async def hook() -> None:
        log.append(name)

    return hook


async def test_lifespan_runs_startup_hooks_in_order_then_completes() -> None:
    log: list[str] = []
    app = App()
    app.on_startup(recorder("one", log))
    app.on_startup(recorder("two", log))

    sent = await run_lifespan(app, "lifespan.startup", "lifespan.shutdown")

    assert log == ["one", "two"]
    assert [m["type"] for m in sent] == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]


async def test_lifespan_runs_shutdown_hooks_in_reverse_order() -> None:
    log: list[str] = []
    app = App()
    app.on_shutdown(recorder("one", log))
    app.on_shutdown(recorder("two", log))

    await run_lifespan(app, "lifespan.startup", "lifespan.shutdown")

    assert log == ["two", "one"]


async def test_lifespan_startup_runs_the_startup_checks_before_the_hooks() -> None:
    log: list[str] = []
    app = App()
    app.get("/a/{id}", text("a"))
    app.get("/a/{other}", text("b"))
    app.on_startup(recorder("hook", log))

    sent = await run_lifespan(app, "lifespan.startup")

    assert [m["type"] for m in sent] == ["lifespan.startup.failed"]
    assert "/a/{other}" in sent[0]["message"]
    assert log == []


async def test_lifespan_startup_hook_failure_reports_failed() -> None:
    async def broken() -> None:
        raise RuntimeError("db down")

    app = App()
    app.on_startup(broken)

    sent = await run_lifespan(app, "lifespan.startup")

    assert [m["type"] for m in sent] == ["lifespan.startup.failed"]
    assert "db down" in sent[0]["message"]


async def test_lifespan_shutdown_failure_still_runs_the_other_hooks() -> None:
    log: list[str] = []

    async def broken() -> None:
        raise RuntimeError("close failed")

    app = App()
    app.on_shutdown(recorder("first", log))
    app.on_shutdown(broken)

    sent = await run_lifespan(app, "lifespan.startup", "lifespan.shutdown")

    assert log == ["first"]
    assert sent[-1]["type"] == "lifespan.shutdown.failed"
    assert "close failed" in sent[-1]["message"]


async def test_non_http_non_lifespan_scope_is_rejected() -> None:
    app = App()

    async def receive() -> Message:
        raise AssertionError

    async def send(message: Message) -> None:
        raise AssertionError

    with pytest.raises(NotImplementedError, match="websocket"):
        await app({"type": "websocket"}, receive, send)


# --- extension points ---------------------------------------------------


async def test_problem_response_includes_extension_members() -> None:
    async def handler(c: Context) -> Response:
        raise HTTPError(
            422, "Unprocessable Entity", "bad", extensions={"errors": [{"path": "$.a"}]}
        )

    app = App()
    app.get("/x", handler)

    async with client(app) as http:
        res = await http.get("/x")

    assert res.json()["errors"] == [{"path": "$.a"}]
    assert res.json()["status"] == 422


def test_startup_problems_reported_by_extensions_are_listed_with_route_problems() -> (
    None
):
    app = App()
    app.get("no-slash", text("a"))
    app.add_startup_problem("contract A is broken")
    app.group("/g").add_startup_problem("contract B is broken")

    with pytest.raises(StartupError) as caught:
        app.build()

    problems = caught.value.problems
    assert len(problems) == 3
    assert "contract A is broken" in problems
    assert "contract B is broken" in problems


def test_startup_problems_cannot_be_added_after_the_app_is_built() -> None:
    app = App()
    app.build()

    with pytest.raises(RuntimeError, match="built"):
        app.add_startup_problem("too late")


def test_app_state_is_shared_with_its_groups() -> None:
    app = App()

    app.state["answer"] = 42

    assert app.group("/g").state is app.state
    assert app.group("/g").group("/h").state["answer"] == 42
