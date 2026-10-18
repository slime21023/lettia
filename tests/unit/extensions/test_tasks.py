import asyncio
import time
from collections.abc import AsyncIterator
from typing import Protocol

import pytest

from lettia import App, Context, Ok, op, public
from lettia.extensions.di import Container, DIMiddleware, Injected
from lettia.extensions.tasks import (
    BackgroundTasks,
    BackgroundTasksMiddleware,
)
from lettia.testing import TestClient

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
