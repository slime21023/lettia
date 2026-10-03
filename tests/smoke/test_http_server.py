import asyncio
import threading
from collections.abc import AsyncIterator

import pytest

from lettia import App, Context
from lettia.response import StreamResponse


@pytest.mark.contract("RESP-HEAD", "RESP-WRITE")
def test_real_server_head_retains_get_length_without_body() -> None:
    from tests.support.server import running_server

    app = App()
    app.add_route("GET", "/", lambda ctx: "hello")
    with running_server(app) as client:
        response = client.head("/")
        assert response.status_code == 200
        assert response.content == b""
        assert response.headers["content-length"] == "5"
        # Reuse the connection to detect stray HEAD body bytes on the wire.
        assert client.get("/").content == b"hello"


@pytest.mark.contract("RESP-STREAM", "APP-COMPLETE")
def test_real_connection_close_cleans_stream_and_skips_background() -> None:
    from tests.support.server import running_server

    closed = threading.Event()
    background = threading.Event()
    app = App()

    async def stream() -> AsyncIterator[bytes]:
        try:
            yield b"first\n"
            await asyncio.Future[None]()
        finally:
            closed.set()

    @app.get("/")
    def route(ctx: Context) -> StreamResponse:
        ctx.add_background_task(background.set)
        return StreamResponse(stream())

    with running_server(app) as client:
        with client.stream("GET", "/") as response:
            assert response.status_code == 200
            assert next(response.iter_bytes()) == b"first\n"
        assert closed.wait(5), "Disconnect did not close the stream"
    assert not background.is_set()


@pytest.mark.contract("APP-LIFESPAN")
def test_real_server_owns_startup_and_shutdown() -> None:
    from tests.support.server import running_server

    events: list[str] = []
    app = App()

    @app.on_event("startup")
    async def startup() -> None:
        events.append("startup")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        events.append("shutdown")

    app.add_route("GET", "/", lambda ctx: "ready")
    with running_server(app) as client:
        assert events == ["startup"]
        assert client.get("/").text == "ready"
    assert events == ["startup", "shutdown"]


@pytest.mark.contract("APP-LIFESPAN")
def test_real_server_startup_failure_reclaims_thread() -> None:
    from tests.support.server import running_server

    app = App()

    @app.on_event("startup")
    def startup() -> None:
        raise RuntimeError("startup rejected")

    with pytest.raises(AssertionError, match="startup failed"):
        with running_server(app):
            pytest.fail("Failed startup must not yield a client")
    assert not any(t.name == "lettia-smoke-server" for t in threading.enumerate())


@pytest.mark.contract("APP-LIFESPAN")
def test_real_server_client_failure_still_runs_shutdown() -> None:
    from tests.support.server import running_server

    stopped = threading.Event()
    app = App()
    app.on_event("shutdown")(stopped.set)
    with pytest.raises(ValueError, match="test client failed"):
        with running_server(app):
            raise ValueError("test client failed")
    assert stopped.is_set()
