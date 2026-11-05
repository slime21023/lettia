import asyncio
import functools
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol

import pytest

from lettia import App, Context, Ok, op, public
from lettia.core import Context as CoreContext
from lettia.core import Message, Response
from lettia.core.handler import Handler, Middleware
from lettia.extensions.di import Container, DIMiddleware, Injected
from lettia.extensions.tasks import (
    BackgroundTasks,
    BackgroundTasksMiddleware,
)
from lettia.testing import TestClient

type CoreHandler = Callable[[CoreContext], Awaitable[Response]]

# --- Test 1: Async task executes after response is sent ---


class AsyncApi(Protocol):
    @op.post("/run-async", guard=public)
    async def run_async(self, c: Context[None], req: None) -> Ok[dict[str, str]]: ...


@pytest.mark.asyncio
async def test_async_background_task_executes() -> None:
    executed_events: list[str] = []

    async def sample_async_job(name: str) -> None:
        await asyncio.sleep(0.01)
        executed_events.append(name)

    class AsyncService(AsyncApi):
        async def run_async(self, c: Context[None], req: None) -> Ok[dict[str, str]]:
            tasks = BackgroundTasks.of(c)
            tasks.add_task(sample_async_job, "email-sent")
            return Ok({"status": "queued"})

    app = App()
    middleware = BackgroundTasksMiddleware()
    app.use(middleware)
    app.mount(AsyncService())

    async with TestClient(app) as client:
        resp = await client.post("/run-async")
        assert resp.status_code == 200
        assert resp.json() == {"status": "queued"}

        # Wait for background task to complete
        await middleware.drain(timeout_sec=1.0)
        assert executed_events == ["email-sent"]


# --- Test 2: Synchronous blocking task runs in thread without blocking event loop ---


class SyncApi(Protocol):
    @op.post("/run-sync", guard=public)
    async def run_sync(self, c: Context[None], req: None) -> Ok[str]: ...


@pytest.mark.asyncio
async def test_sync_background_task_runs_in_thread() -> None:
    results: list[int] = []

    def blocking_job(val: int) -> None:
        time.sleep(0.02)
        results.append(val)

    class SyncService(SyncApi):
        async def run_sync(self, c: Context[None], req: None) -> Ok[str]:
            tasks = BackgroundTasks.of(c)
            tasks.add_task(blocking_job, 42)
            return Ok("ok")

    app = App()
    middleware = BackgroundTasksMiddleware()
    app.use(middleware)
    app.mount(SyncService())

    async with TestClient(app) as client:
        resp = await client.post("/run-sync")
        assert resp.status_code == 200
        assert resp.json() == "ok"

        await middleware.drain(timeout_sec=1.0)
        assert results == [42]


# --- Test 3: Multiple tasks execute in sequential FIFO order ---


class FifoApi(Protocol):
    @op.post("/fifo", guard=public)
    async def fifo(self, c: Context[None], req: None) -> Ok[str]: ...


@pytest.mark.asyncio
async def test_tasks_run_in_fifo_order() -> None:
    order: list[int] = []

    async def step(index: int) -> None:
        await asyncio.sleep(0.01)
        order.append(index)

    class FifoService(FifoApi):
        async def fifo(self, c: Context[None], req: None) -> Ok[str]:
            tasks = BackgroundTasks.of(c)
            tasks.add_task(step, 1)
            tasks.add_task(step, 2)
            tasks.add_task(step, 3)
            return Ok("ordered")

    app = App()
    middleware = BackgroundTasksMiddleware()
    app.use(middleware)
    app.mount(FifoService())

    async with TestClient(app) as client:
        resp = await client.post("/fifo")
        assert resp.status_code == 200
        assert resp.json() == "ordered"

        await middleware.drain(timeout_sec=1.0)
        assert order == [1, 2, 3]


# --- Test 4: Task exception is isolated and does not abort others ---


class FailingTaskApi(Protocol):
    @op.post("/failing-task", guard=public)
    async def failing_task(self, c: Context[None], req: None) -> Ok[str]: ...


@pytest.mark.asyncio
async def test_exception_in_background_task_is_isolated() -> None:
    completed: list[str] = []

    async def bad_job() -> None:
        raise ValueError("simulated background task failure")

    async def good_job() -> None:
        completed.append("good")

    class FailingTaskService(FailingTaskApi):
        async def failing_task(self, c: Context[None], req: None) -> Ok[str]:
            tasks = BackgroundTasks.of(c)
            tasks.add_task(bad_job)
            tasks.add_task(good_job)
            return Ok("dispatched")

    app = App()
    middleware = BackgroundTasksMiddleware()
    app.use(middleware)
    app.mount(FailingTaskService())

    async with TestClient(app) as client:
        resp = await client.post("/failing-task")
        assert resp.status_code == 200
        assert resp.json() == "dispatched"

        # Even though bad_job failed, good_job must execute!
        await middleware.drain(timeout_sec=1.0)
        assert completed == ["good"]


# --- Test 5: Injected[BackgroundTasks] integration with lettia.extensions.di ---


class DiTaskApi(Protocol):
    @op.post("/di-task", guard=public)
    async def di_task(self, c: Context[None], req: None) -> Ok[str]: ...


@pytest.mark.asyncio
async def test_injected_background_tasks_via_di() -> None:
    events: list[str] = []

    async def log_event(event_name: str) -> None:
        events.append(event_name)

    class DiTaskService(DiTaskApi):
        tasks: Injected[BackgroundTasks]

        async def di_task(self, c: Context[None], req: None) -> Ok[str]:
            self.tasks.add_task(log_event, "audit_logged")
            return Ok("di-ok")

    container = Container()
    app = App()
    task_mw = BackgroundTasksMiddleware()
    app.use(task_mw)
    app.use(DIMiddleware(container))
    container.wire(app, [DiTaskService])

    async with TestClient(app) as client:
        resp = await client.post("/di-task")
        assert resp.status_code == 200
        assert resp.json() == "di-ok"

        await task_mw.drain(timeout_sec=1.0)
        assert events == ["audit_logged"]


# --- Test 6: BackgroundTasks.of raises RuntimeError if middleware is missing ---


def test_missing_middleware_raises_error() -> None:
    from lettia.core import Context as CoreContext
    from lettia.core import Request

    request = Request(
        method="GET",
        path="/",
        raw_path=b"/",
        query_string=b"",
        headers=None,
        client=None,
        body=_empty_body(),
    )
    c = CoreContext(request)
    with pytest.raises(RuntimeError, match="BackgroundTasksMiddleware"):
        _ = BackgroundTasks.of(c)


# --- Test 7: Lifespan shutdown automatically drains active tasks ---


class ShutdownDrainApi(Protocol):
    @op.post("/drain-test", guard=public)
    async def drain_test(self, c: Context[None], req: None) -> Ok[str]: ...


@pytest.mark.asyncio
async def test_lifespan_shutdown_drains_tasks() -> None:
    drained_events: list[str] = []

    async def slow_job() -> None:
        await asyncio.sleep(0.05)
        drained_events.append("completed_before_shutdown")

    class ShutdownDrainService(ShutdownDrainApi):
        async def drain_test(self, c: Context[None], req: None) -> Ok[str]:
            tasks = BackgroundTasks.of(c)
            tasks.add_task(slow_job)
            return Ok("started")

    app = App()
    task_mw = BackgroundTasksMiddleware(app)
    app.use(task_mw)
    app.mount(ShutdownDrainService())

    async with TestClient(app) as client:
        resp = await client.post("/drain-test")
        assert resp.status_code == 200
        # Immediately after post, slow_job has not finished yet
        assert "completed_before_shutdown" not in drained_events

    # After TestClient exits (shutdown completes), task must have been drained!
    assert drained_events == ["completed_before_shutdown"]


async def _empty_body() -> AsyncIterator[bytes]:
    if False:
        yield b""


# --- Test 8: sync callables that return awaitables are awaited ---


class _AsyncCallable:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def __call__(self, name: str) -> None:
        self._events.append(name)


@pytest.mark.asyncio
async def test_sync_callables_returning_a_coroutine_are_awaited() -> None:
    events: list[str] = []

    async def job(name: str) -> None:
        events.append(name)

    tasks = BackgroundTasks()
    tasks.add_task(lambda: job("lambda"))
    tasks.add_task(functools.partial(job, "partial"))
    tasks.add_task(_AsyncCallable(events), "callable-object")

    await tasks.run()

    assert events == ["lambda", "partial", "callable-object"]


# --- Scheduling happens after the response was sent ---


def _http_scope(path: str = "/") -> dict[str, Any]:
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 5000),
    }


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


def _core_app(
    handler: CoreHandler,
    *,
    outer: tuple[Middleware, ...] = (),
    inner: tuple[Middleware, ...] = (),
    path: str = "/",
) -> tuple[App, BackgroundTasksMiddleware]:
    app = App()
    middleware = BackgroundTasksMiddleware()
    app.use(*outer, middleware, *inner)
    app.get(path, handler)
    return app, middleware


def _queueing(job: Callable[[], Awaitable[None]]) -> CoreHandler:
    async def handler(c: CoreContext) -> Response:
        BackgroundTasks.of(c).add_task(job)
        return Response(200, b"ok")

    return handler


def _with_after_send(callback: Callable[[], None]) -> Middleware:
    def middleware(next_handler: Handler) -> Handler:
        async def run(c: CoreContext) -> Response:
            response = await next_handler(c)
            response.after_send = callback
            return response

        return run

    return middleware


async def test_tasks_start_only_after_the_response_was_sent() -> None:
    events: list[str] = []
    release = asyncio.Event()

    async def job() -> None:
        events.append("job-start")
        await release.wait()
        events.append("job-end")

    app, middleware = _core_app(_queueing(job))

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            for _ in range(3):  # a task scheduled earlier would run here
                await asyncio.sleep(0)
            events.append("final-body")

    await app(_http_scope(), _receive, send)
    events.append("returned")

    assert events == ["final-body", "returned"]
    await asyncio.sleep(0)
    assert events == ["final-body", "returned", "job-start"]
    release.set()
    await middleware.drain(timeout_sec=1.0)
    assert events == ["final-body", "returned", "job-start", "job-end"]


class _FailingClose:
    def __aiter__(self) -> "_FailingClose":
        return self

    async def __anext__(self) -> bytes:
        raise StopAsyncIteration

    async def aclose(self) -> None:
        raise RuntimeError("close failed")


async def _raising_stream() -> AsyncIterator[bytes]:
    yield b"a"
    raise RuntimeError("stream failed")


@pytest.mark.parametrize(
    "failure", ["send-start", "send-body", "stream-iteration", "stream-close"]
)
async def test_tasks_do_not_start_when_the_response_fails(failure: str) -> None:
    events: list[str] = []

    async def job() -> None:
        events.append("job")

    async def handler(c: CoreContext) -> Response:
        BackgroundTasks.of(c).add_task(job)
        if failure == "stream-iteration":
            return Response(200, _raising_stream())
        if failure == "stream-close":
            return Response(200, _FailingClose())
        return Response(200, b"ok")

    app, middleware = _core_app(handler)

    async def send(message: Message) -> None:
        if failure == "send-start" and message["type"] == "http.response.start":
            raise ConnectionResetError
        if failure == "send-body" and message["type"] == "http.response.body":
            raise ConnectionResetError

    with pytest.raises((ConnectionResetError, RuntimeError)):
        await app(_http_scope(), _receive, send)
    await asyncio.sleep(0)
    await middleware.drain(timeout_sec=1.0)

    assert events == []


async def test_tasks_do_not_start_when_the_request_is_cancelled_while_sending() -> None:
    events: list[str] = []
    sending = asyncio.Event()
    never = asyncio.Event()

    async def job() -> None:
        events.append("job")

    app, middleware = _core_app(_queueing(job))

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            sending.set()
            await never.wait()

    request = asyncio.create_task(app(_http_scope(), _receive, send))
    await sending.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    await asyncio.sleep(0)
    await middleware.drain(timeout_sec=1.0)

    assert events == []


async def test_tasks_queued_before_a_handler_error_run_once_the_500_is_sent() -> None:
    events: list[str] = []
    statuses: list[int] = []

    async def job() -> None:
        events.append("job")

    async def handler(c: CoreContext) -> Response:
        BackgroundTasks.of(c).add_task(job)
        raise ValueError("boom")

    app, middleware = _core_app(handler)

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    await app(_http_scope(), _receive, send)
    await middleware.drain(timeout_sec=1.0)

    assert statuses == [500]
    assert events == ["job"]


async def test_tasks_of_a_replaced_response_do_not_run() -> None:
    events: list[str] = []

    async def job() -> None:
        events.append("job")

    def replace(next_handler: Handler) -> Handler:
        async def run(c: CoreContext) -> Response:
            await next_handler(c)
            return Response(204)

        return run

    app, middleware = _core_app(_queueing(job), outer=(replace,))

    async def send(message: Message) -> None:
        return None

    await app(_http_scope(), _receive, send)
    await middleware.drain(timeout_sec=1.0)

    assert events == []


async def test_tasks_run_after_an_existing_after_send_callback() -> None:
    events: list[str] = []

    async def job() -> None:
        events.append("job")

    app, middleware = _core_app(
        _queueing(job), inner=(_with_after_send(lambda: events.append("previous")),)
    )

    async def send(message: Message) -> None:
        return None

    await app(_http_scope(), _receive, send)
    await middleware.drain(timeout_sec=1.0)

    assert events == ["previous", "job"]


async def test_tasks_still_start_when_the_previous_callback_raises() -> None:
    events: list[str] = []

    async def job() -> None:
        events.append("job")

    def failing() -> None:
        raise RuntimeError("callback failed")

    app, middleware = _core_app(_queueing(job), inner=(_with_after_send(failing),))

    async def send(message: Message) -> None:
        return None

    await app(_http_scope(), _receive, send)
    await middleware.drain(timeout_sec=1.0)

    assert events == ["job"]


async def test_tasks_do_not_start_when_the_previous_callback_is_cancelled() -> None:
    events: list[str] = []

    async def job() -> None:
        events.append("job")

    def cancelled() -> None:
        raise asyncio.CancelledError

    app, middleware = _core_app(_queueing(job), inner=(_with_after_send(cancelled),))

    async def send(message: Message) -> None:
        return None

    with pytest.raises(asyncio.CancelledError):
        await app(_http_scope(), _receive, send)
    await asyncio.sleep(0)
    await middleware.drain(timeout_sec=1.0)

    assert events == []


async def test_concurrent_requests_start_only_their_own_batch() -> None:
    started: list[str] = []
    gates = {"a": asyncio.Event(), "b": asyncio.Event()}
    blocked = {"a": asyncio.Event(), "b": asyncio.Event()}

    async def job(name: str) -> None:
        started.append(name)

    async def handler(c: CoreContext) -> Response:
        BackgroundTasks.of(c).add_task(job, c.request.path[1:])
        return Response(200, b"ok")

    app = App()
    middleware = BackgroundTasksMiddleware()
    app.use(middleware)
    app.get("/a", handler)
    app.get("/b", handler)

    def sender(name: str) -> Callable[[Message], Awaitable[None]]:
        async def send(message: Message) -> None:
            if message["type"] == "http.response.body":
                blocked[name].set()
                await gates[name].wait()

        return send

    request_a = asyncio.create_task(app(_http_scope("/a"), _receive, sender("a")))
    request_b = asyncio.create_task(app(_http_scope("/b"), _receive, sender("b")))
    await blocked["a"].wait()
    await blocked["b"].wait()

    gates["a"].set()
    await request_a
    await middleware.drain(timeout_sec=1.0)
    assert started == ["a"]

    gates["b"].set()
    await request_b
    await middleware.drain(timeout_sec=1.0)
    assert started == ["a", "b"]


async def test_a_running_task_can_queue_more_tasks_on_its_own_batch() -> None:
    events: list[str] = []

    async def second() -> None:
        events.append("second")

    async def first() -> None:
        events.append("first")
        BackgroundTasks.__injected__().add_task(second)

    app, middleware = _core_app(_queueing(first))

    async def send(message: Message) -> None:
        return None

    await app(_http_scope(), _receive, send)
    await middleware.drain(timeout_sec=1.0)

    assert events == ["first", "second"]


# --- Request-scoped resources are gone by the time a task runs ---


class _Session:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class ScopedTaskApi(Protocol):
    @op.post("/scoped-task", guard=public)
    async def scoped_task(self, c: Context[None], req: None) -> Ok[str]: ...


@pytest.mark.parametrize("tasks_outer", [True, False])
async def test_tasks_run_after_scope_teardown_and_cannot_use_injected(
    tasks_outer: bool,
) -> None:
    seen: dict[str, object] = {}

    async def inspect_session(session: _Session) -> None:
        seen["closed"] = session.closed

    class ScopedTaskService(ScopedTaskApi):
        tasks: Injected[BackgroundTasks]
        db: Injected[_Session]

        async def scoped_task(self, c: Context[None], req: None) -> Ok[str]:
            self.tasks.add_task(inspect_session, self.db)
            self.tasks.add_task(self.read_db)
            return Ok("queued")

        async def read_db(self) -> None:
            try:
                _ = self.db
            except RuntimeError as exc:
                seen["injected"] = str(exc)

    container = Container()
    container.add_scoped_by_factory(_Session, _Session)
    task_mw = BackgroundTasksMiddleware()
    app = App()
    if tasks_outer:
        app.use(task_mw, DIMiddleware(container))
    else:
        app.use(DIMiddleware(container), task_mw)
    container.wire(app, [ScopedTaskService])

    async with TestClient(app) as client:
        resp = await client.post("/scoped-task")
        assert resp.status_code == 200
        await task_mw.drain(timeout_sec=1.0)

    assert seen["closed"] is True
    assert "outside of an active request scope" in str(seen["injected"])


# --- Shutdown: shared lifespan resources outlive the drain ---


async def test_shutdown_drains_tasks_before_lifespan_resources_close() -> None:
    log: list[str] = []

    @asynccontextmanager
    async def pool() -> AsyncIterator[None]:
        log.append("open")
        try:
            yield
        finally:
            log.append("close")

    async def job() -> None:
        await asyncio.sleep(0.01)
        log.append("task-done")

    async def handler(c: CoreContext) -> Response:
        BackgroundTasks.of(c).add_task(job)
        return Response(202)

    app = App()
    app.lifespan(pool)
    app.use(BackgroundTasksMiddleware(app))
    app.post("/work", handler)

    async with TestClient(app) as client:
        resp = await client.post("/work")
        assert resp.status_code == 202

    assert log == ["open", "task-done", "close"]
