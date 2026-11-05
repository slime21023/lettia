"""Middleware managing BackgroundTasks lifecycle and background execution."""

import asyncio
from collections.abc import Callable
from typing import cast

from lettia.core import App, Context, Handler, Response
from lettia.extensions.tasks.manager import (
    TASKS_KEY,
    BackgroundTasks,
    current_tasks,
)


class BackgroundTasksMiddleware:
    """Per-request BackgroundTasks collection runner."""

    def __init__(
        self, app: App | None = None, *, shutdown_timeout_sec: float = 10.0
    ) -> None:
        self._active_tasks: set[asyncio.Task[None]] = set()
        self._shutdown_timeout_sec = shutdown_timeout_sec
        if app is not None:
            self.attach(app)

    def attach(self, app: App) -> None:
        """Register the drain hook on app's lifespan shutdown."""
        app.on_shutdown(self.drain)

    async def drain(self, timeout_sec: float | None = None) -> None:
        """Wait for all currently active background tasks to complete."""
        if not self._active_tasks:
            return
        effective_sec = (
            timeout_sec if timeout_sec is not None else self._shutdown_timeout_sec
        )
        to_wait = list(self._active_tasks)
        await asyncio.wait(to_wait, timeout=effective_sec)

    def __call__(self, next_handler: Handler) -> Handler:
        async def run(c: Context) -> Response:
            tasks = BackgroundTasks()
            TASKS_KEY.set(c, tasks)
            token = current_tasks.set(tasks)
            try:
                response = await next_handler(c)
            finally:
                current_tasks.reset(token)
            if tasks:
                response.after_send = self._start_after(response.after_send, tasks)
            return response

        return cast(Handler, run)

    def _start_after(
        self, previous: Callable[[], None] | None, tasks: BackgroundTasks
    ) -> Callable[[], None]:
        """Wrap ``previous`` so the batch starts once the response was sent."""

        def start() -> None:
            if previous is not None:
                try:
                    previous()
                except Exception:
                    self._start(tasks)  # the batch must not depend on the callback
                    raise
            self._start(tasks)

        return start

    def _start(self, tasks: BackgroundTasks) -> None:
        runner = asyncio.create_task(self._run(tasks))
        self._active_tasks.add(runner)
        runner.add_done_callback(self._active_tasks.discard)

    @staticmethod
    async def _run(tasks: BackgroundTasks) -> None:
        token = current_tasks.set(tasks)
        try:
            await tasks.run()
        finally:
            current_tasks.reset(token)
