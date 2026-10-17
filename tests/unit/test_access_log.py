import logging
import re
from collections.abc import AsyncIterator

import httpx
import pytest

from lettia.core import (
    App,
    ClientDisconnect,
    Context,
    Handler,
    Middleware,
    Request,
    Response,
)
from lettia.middleware import access_log, request_id

LOGGER = "lettia.access"


def client(app: App) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def hello(c: Context) -> Response:
    return c.text(200, "hello")


def build(*middleware: Middleware) -> App:
    app = App()
    app.use(*middleware)
    app.get("/hello", hello)
    return app


def attr(record: logging.LogRecord, name: str) -> object:
    """An ``extra`` field of a log record, which is not a declared attribute."""
    return record.__dict__[name]


def records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name.startswith("lettia.access")]


async def test_access_log_writes_one_info_record_per_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER):
        async with client(build(access_log())) as http:
            await http.get("/hello")

    [record] = records(caplog)
    assert record.levelno == logging.INFO
    assert re.fullmatch(r"GET /hello 200 \d+\.\d+ms", record.getMessage())


async def test_access_log_record_carries_structured_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER):
        async with client(build(access_log())) as http:
            await http.get("/hello")

    [record] = records(caplog)
    assert attr(record, "method") == "GET"
    assert attr(record, "path") == "/hello"
    assert attr(record, "status") == 200
    duration = attr(record, "duration_ms")
    assert isinstance(duration, float)
    assert duration >= 0
    assert attr(record, "client") == "127.0.0.1"
    assert attr(record, "request_id") is None


async def test_access_log_omits_the_query_string(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER):
        async with client(build(access_log())) as http:
            await http.get("/hello?token=secret")

    [record] = records(caplog)
    assert attr(record, "path") == "/hello"
    assert "secret" not in record.getMessage()


async def test_access_log_records_the_raw_path_so_encoded_newlines_stay_inert(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER):
        async with client(build(access_log())) as http:
            await http.get("/forged%0Aline")

    [record] = records(caplog)
    assert attr(record, "path") == "/forged%0Aline"
    assert "\n" not in record.getMessage()


async def test_access_log_includes_the_request_id_when_one_is_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = build(request_id(generate=lambda: "rid-1"), access_log())

    with caplog.at_level(logging.INFO, logger=LOGGER):
        async with client(app) as http:
            await http.get("/hello")

    [record] = records(caplog)
    assert attr(record, "request_id") == "rid-1"
    assert record.getMessage().endswith("request_id=rid-1")


async def test_access_log_covers_not_found_and_error_responses(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def boom(c: Context) -> Response:
        raise ValueError("x")

    app = build(access_log())
    app.get("/boom", boom)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        async with client(app) as http:
            await http.get("/missing")
            await http.get("/boom")

    statuses = [attr(r, "status") for r in records(caplog)]
    assert statuses == [404, 500]


async def test_access_log_logs_a_500_when_inner_middleware_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(next_: Handler) -> Handler:
        async def run(c: Context) -> Response:
            raise RuntimeError("inner bug")

        return run

    with caplog.at_level(logging.INFO, logger=LOGGER):
        async with client(build(access_log(), broken)) as http:
            res = await http.get("/hello")

    [record] = records(caplog)
    assert attr(record, "status") == 500
    assert res.status_code == 500


async def test_access_log_writes_to_a_custom_logger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    custom = logging.getLogger("my.app.requests")

    with caplog.at_level(logging.INFO, logger="my.app.requests"):
        async with client(build(access_log(logger=custom))) as http:
            await http.get("/hello")

    assert [r.name for r in caplog.records] == ["my.app.requests"]


async def test_access_log_stays_silent_below_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        async with client(build(access_log())) as http:
            await http.get("/hello")

    assert records(caplog) == []


async def test_access_log_records_499_and_reraises_on_client_disconnect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def no_body() -> AsyncIterator[bytes]:
        return
        yield b""  # pragma: no cover - makes this an async generator

    async def handler(c: Context) -> Response:
        raise ClientDisconnect

    c = Context(Request(method="GET", path="/", body=no_body()))

    with caplog.at_level(logging.INFO, logger=LOGGER):
        with pytest.raises(ClientDisconnect):
            await access_log()(handler)(c)

    [record] = records(caplog)
    assert attr(record, "status") == 499
