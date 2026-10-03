"""A bounded real-server lifetime with an already reserved loopback socket."""

import asyncio
import socket
import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import override

import httpx
import uvicorn

from lettia import App


class _Server(uvicorn.Server):
    def __init__(self, app: App) -> None:
        super().__init__(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=0,
                lifespan="on",
                ws="none",
                log_level="error",
                timeout_graceful_shutdown=2,
            )
        )
        self.ready = threading.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task[None] | None = None

    @override
    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        self.ready.set()

    async def run_owned(self, listener: socket.socket) -> None:
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        await self.serve(sockets=[listener])

    def cancel(self) -> None:
        if (
            self.loop is not None
            and self.task is not None
            and not self.loop.is_closed()
        ):
            self.loop.call_soon_threadsafe(self.task.cancel)


@contextmanager
def running_server(app: App) -> Generator[httpx.Client, None, None]:
    server = _Server(app)
    errors: list[BaseException] = []
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        address = listener.getsockname()

        def run() -> None:
            try:
                asyncio.run(server.run_owned(listener))
            except BaseException as exc:
                # Include startup SystemExit and cancellations; report them in
                # the test thread instead of silently losing thread failures.
                errors.append(exc)
            finally:
                server.ready.set()

        thread = threading.Thread(target=run, name="lettia-smoke-server", daemon=True)
        thread.start()
        try:
            if not server.ready.wait(5):
                raise AssertionError("Uvicorn startup exceeded 5 seconds")
            if errors:
                raise AssertionError("Uvicorn startup failed") from errors[0]
            if not server.started:
                raise AssertionError("Uvicorn did not start listening")
            with httpx.Client(
                base_url=f"http://127.0.0.1:{address[1]}",
                timeout=5,
                trust_env=False,
            ) as client:
                yield client
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            forced = thread.is_alive()
            if forced:
                server.force_exit = True
                server.cancel()
                thread.join(timeout=5)
            if thread.is_alive():
                raise AssertionError("Uvicorn thread survived forced cleanup")
            if forced:
                raise AssertionError("Uvicorn required forced shutdown")
        if errors:
            raise AssertionError(
                "Uvicorn failed during request or shutdown"
            ) from errors[0]
