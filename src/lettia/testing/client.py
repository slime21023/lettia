"""``TestClient``: an httpx client that talks to an app and runs its lifespan."""

import asyncio
from types import TracebackType
from typing import Any, Self, override

import httpx

from lettia.app import App
from lettia.core.asgi import Message


class _Lifespan:
    """Drives the ASGI lifespan protocol of an app from the outside."""

    def __init__(self, app: App) -> None:
        self._app = app
        self._to_app: asyncio.Queue[Message] = asyncio.Queue()
        self._from_app: asyncio.Queue[Message] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(
            self._app(
                {"type": "lifespan", "asgi": {"version": "3.0"}},
                self._to_app.get,
                self._from_app.put,
            )
        )
        await self._to_app.put({"type": "lifespan.startup"})
        reply = await self._next()
        if reply["type"] == "lifespan.startup.failed":
            await self._finish()
            raise RuntimeError(reply.get("message", "startup failed"))

    async def stop(self) -> None:
        await self._to_app.put({"type": "lifespan.shutdown"})
        reply = await self._next()
        await self._finish()
        if reply["type"] == "lifespan.shutdown.failed":
            raise RuntimeError(reply.get("message", "shutdown failed"))

    async def _next(self) -> Message:
        task = self._task
        if task is None:
            raise RuntimeError("the lifespan was not started")
        reply = asyncio.create_task(self._from_app.get())
        done, _ = await asyncio.wait({reply, task}, return_when=asyncio.FIRST_COMPLETED)
        if reply in done:
            return reply.result()
        reply.cancel()
        task.result()  # re-raises whatever stopped the application
        raise RuntimeError("the application ended without answering the lifespan")

    async def _finish(self) -> None:
        if self._task is not None:
            await self._task


class TestClient(httpx.AsyncClient):
    """Sends requests straight to an app, with startup and shutdown around them.

    Entering ``async with`` builds the app, so startup problems raise right
    there, and then runs the startup hooks; leaving runs the shutdown hooks.
    """

    __test__ = False  # not a test class, whatever pytest thinks of the name

    def __init__(
        self, app: App, *, base_url: str = "http://testserver", **options: Any
    ) -> None:
        super().__init__(
            transport=httpx.ASGITransport(app=app), base_url=base_url, **options
        )
        self._lifespan = _Lifespan(app)
        self._app = app

    @override
    async def __aenter__(self) -> Self:
        self._app.build()
        await self._lifespan.start()
        try:
            await super().__aenter__()
        except BaseException:
            await self._lifespan.stop()
            raise
        return self

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        try:
            await super().__aexit__(exc_type, exc_value, traceback)
        finally:
            await self._lifespan.stop()
