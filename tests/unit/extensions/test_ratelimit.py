import asyncio
from collections.abc import AsyncIterator
from typing import Any, override

import pytest

from lettia.core import App, Context, Request, RequestHead, Response
from lettia.extensions.ratelimit import client_ip, rate_limit
from lettia.testing import TestClient


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def who(head: RequestHead) -> str | None:
    return head.headers.get("x-who")


async def ok(c: Context) -> Response:
    return c.text(200, "ok")


def make_app(
    rule: str = "2/second",
    clock: Clock | None = None,
    *,
    key: Any = who,
    max_keys: int = 10_000,
) -> App:
    app = App()
    app.use(rate_limit(rule, key=key, max_keys=max_keys, clock=clock or Clock()))
    app.get("/", ok)
    return app


async def no_body() -> AsyncIterator[bytes]:
    return
    yield b""


def head(client: tuple[str, int] | None, **headers: str) -> Request:
    raw = [(k.replace("_", "-").encode(), v.encode()) for k, v in headers.items()]
    return Request(method="GET", path="/", body=no_body(), headers=raw, client=client)


async def call(app: App, who_: str | None = "a", path: str = "/") -> Any:
    async with TestClient(app) as http:
        headers = {} if who_ is None else {"x-who": who_}
        return await http.get(path, headers=headers)


async def test_rate_limit_rejects_third_request_and_recovers_at_boundary() -> None:
    clock = Clock()
    app = make_app("2/second", clock)
    async with TestClient(app) as http:
        first = await http.get("/", headers={"x-who": "a"})
        second = await http.get("/", headers={"x-who": "a"})
        third = await http.get("/", headers={"x-who": "a"})
        clock.now += 0.5
        still = await http.get("/", headers={"x-who": "a"})
        clock.now = 101.0
        recovered = await http.get("/", headers={"x-who": "a"})
    assert [first.status_code, second.status_code] == [200, 200]
    assert first.headers["x-ratelimit-limit"] == "2"
    assert first.headers["x-ratelimit-remaining"] == "1"
    assert second.headers["x-ratelimit-remaining"] == "0"
    assert third.status_code == 429
    assert third.headers["content-type"] == "application/problem+json"
    assert third.headers["x-ratelimit-remaining"] == "0"
    assert third.headers["retry-after"] == "1"
    assert still.status_code == 429
    assert recovered.status_code == 200


async def test_rate_limit_retry_after_rounds_up() -> None:
    clock = Clock()
    app = make_app("1/minute", clock)
    async with TestClient(app) as http:
        await http.get("/", headers={"x-who": "a"})
        clock.now += 0.2
        res = await http.get("/", headers={"x-who": "a"})
    assert res.headers["retry-after"] == "60"


async def test_rate_limit_isolates_identities() -> None:
    app = make_app("1/minute")
    async with TestClient(app) as http:
        a = await http.get("/", headers={"x-who": "a"})
        b = await http.get("/", headers={"x-who": "b"})
        again = await http.get("/", headers={"x-who": "a"})
    assert (a.status_code, b.status_code, again.status_code) == (200, 200, 429)


async def test_rate_limit_missing_identity_shares_the_unknown_bucket() -> None:
    app = make_app("1/minute")
    async with TestClient(app) as http:
        first = await http.get("/")
        second = await http.get("/", headers={"x-who": ""})
    assert (first.status_code, second.status_code) == (200, 429)


class AlwaysEqual:
    """Not a str, yet equal to everything, the empty string included."""

    @override
    def __eq__(self, other: object) -> bool:
        return True

    __hash__ = None  # type: ignore[assignment]


async def test_rate_limit_key_returning_a_non_str_is_rejected_not_pooled() -> None:
    mw = rate_limit("1/minute", key=lambda _: AlwaysEqual(), clock=Clock())  # type: ignore[arg-type,return-value]

    async def inner(c: Context) -> Response:
        return c.text(200, "ok")

    with pytest.raises(TypeError, match="AlwaysEqual"):
        await mw(inner)(Context(head(("10.0.0.1", 1))))


async def test_rate_limit_concurrent_requests_never_exceed_quota() -> None:
    app = make_app("3/minute")
    async with TestClient(app) as http:
        results = await asyncio.gather(
            *(http.get("/", headers={"x-who": "a"}) for _ in range(20))
        )
    assert sorted(r.status_code for r in results).count(200) == 3


async def test_rate_limit_client_ip_ignores_forwarded_headers() -> None:
    mw = rate_limit("1/minute", clock=Clock())
    seen: list[str] = []

    async def inner(c: Context) -> Response:
        seen.append(client_ip(c.request))
        return c.text(200, "ok")

    run = mw(inner)
    peer = ("10.0.0.1", 1)
    first = await run(Context(head(peer, x_forwarded_for="1.1.1.1")))
    second = await run(Context(head(peer, x_forwarded_for="2.2.2.2")))
    other = await run(Context(head(("10.0.0.2", 1))))
    assert (first.status, second.status, other.status) == (200, 429, 200)
    assert seen == ["10.0.0.1", "10.0.0.2"]


def test_rate_limit_client_ip_without_peer_is_unknown() -> None:
    assert client_ip(head(None)) == "unknown"
    assert client_ip(head(("", 1))) == "unknown"


async def test_rate_limit_missing_peer_still_counts_against_unknown() -> None:
    run = rate_limit("1/minute", clock=Clock())(ok)
    assert (await run(Context(head(None)))).status == 200
    assert (await run(Context(head(None)))).status == 429


async def test_rate_limit_key_returning_wrong_type_raises() -> None:
    run = rate_limit("1/minute", key=lambda h: 5, clock=Clock())(ok)  # type: ignore[arg-type,return-value]
    with pytest.raises(TypeError):
        await run(Context(head(None)))


async def test_rate_limit_key_exception_does_not_bypass() -> None:
    def explode(h: RequestHead) -> str:
        raise LookupError("nope")

    app = make_app(key=explode)
    res = await call(app)
    assert res.status_code == 500


async def test_rate_limit_long_identities_are_hashed_without_colliding() -> None:
    app = make_app("1/minute")
    long_a = "x" * 129
    short = "x" * 128
    async with TestClient(app) as http:
        results = [
            (await http.get("/", headers={"x-who": value})).status_code
            for value in (long_a, short, "y" * 129, long_a)
        ]
    assert results == [200, 200, 200, 429]


async def test_rate_limit_capacity_exhaustion_returns_503_without_eviction() -> None:
    clock = Clock()
    app = make_app("1/minute", clock, max_keys=2)
    async with TestClient(app) as http:
        await http.get("/", headers={"x-who": "a"})
        clock.now += 10
        await http.get("/", headers={"x-who": "b"})
        full = await http.get("/", headers={"x-who": "c"})
        a_again = await http.get("/", headers={"x-who": "a"})
        clock.now = 100 + 60
        fresh = await http.get("/", headers={"x-who": "c"})
        b_again = await http.get("/", headers={"x-who": "b"})
    assert full.status_code == 503
    assert full.headers["retry-after"] == "50"
    assert full.headers["content-type"] == "application/problem+json"
    assert "x-ratelimit-limit" not in full.headers
    assert a_again.status_code == 429
    assert fresh.status_code == 200
    assert b_again.status_code == 429


async def test_rate_limit_rejections_do_not_extend_the_window() -> None:
    clock = Clock()
    app = make_app("1/second", clock)
    async with TestClient(app) as http:
        await http.get("/", headers={"x-who": "a"})
        for _ in range(5):
            clock.now += 0.1
            assert (await http.get("/", headers={"x-who": "a"})).status_code == 429
        clock.now = 101.0
        res = await http.get("/", headers={"x-who": "a"})
    assert res.status_code == 200


async def test_rate_limit_counts_error_responses() -> None:
    async def fail(c: Context) -> Response:
        raise RuntimeError("boom")

    app = App()
    app.use(rate_limit("1/minute", key=who, clock=Clock()))
    app.get("/fail", fail)
    async with TestClient(app) as http:
        first = await http.get("/fail", headers={"x-who": "a"})
        second = await http.get("/fail", headers={"x-who": "a"})
    assert (first.status_code, second.status_code) == (500, 429)
    assert first.headers["x-ratelimit-remaining"] == "0"


async def test_rate_limit_app_wide_applies_to_missing_routes_but_group_does_not() -> (
    None
):
    app = App()
    app.use(rate_limit("1/minute", key=who, clock=Clock()))
    app.get("/", ok)
    async with TestClient(app) as http:
        await http.get("/nope", headers={"x-who": "a"})
        second = await http.get("/nope", headers={"x-who": "a"})
    assert second.status_code == 429

    grouped = App()
    group = grouped.group("/g", rate_limit("1/minute", key=who, clock=Clock()))
    group.get("/x", ok)
    grouped.get("/free", ok)
    async with TestClient(grouped) as http:
        results = [
            (await http.get(path, headers={"x-who": "a"})).status_code
            for path in ("/g/x", "/g/x", "/free", "/free")
        ]
    assert results[:2] == [200, 429]
    assert results[2:4] == [200, 200]


async def test_rate_limit_separate_factories_have_independent_quota() -> None:
    app = App()
    one = app.group("/one", rate_limit("1/minute", key=who, clock=Clock()))
    two = app.group("/two", rate_limit("1/minute", key=who, clock=Clock()))
    one.get("/", ok)
    two.get("/", ok)
    async with TestClient(app) as http:
        a = await http.get("/one/", headers={"x-who": "a"})
        b = await http.get("/two/", headers={"x-who": "a"})
    assert (a.status_code, b.status_code) == (200, 200)


async def test_rate_limit_stacked_limiters_both_apply_and_outer_header_wins() -> None:
    app = App()
    app.use(
        rate_limit("5/minute", key=who, clock=Clock()),
        rate_limit("1/minute", key=who, clock=Clock()),
    )
    app.get("/", ok)
    async with TestClient(app) as http:
        first = await http.get("/", headers={"x-who": "a"})
        second = await http.get("/", headers={"x-who": "a"})
    assert first.headers["x-ratelimit-limit"] == "5"
    assert second.status_code == 429


async def test_rate_limit_rejection_survives_custom_error_handler() -> None:
    async def custom(c: Context, exc: Exception) -> Response:
        return c.text(418, "teapot")

    app = App(error_handler=custom)
    app.use(rate_limit("1/minute", key=who, clock=Clock()))
    app.get("/", ok)
    async with TestClient(app) as http:
        await http.get("/", headers={"x-who": "a"})
        res = await http.get("/", headers={"x-who": "a"})
    assert res.status_code == 429
    assert res.headers["retry-after"] == "60"


@pytest.mark.parametrize(
    "rule",
    ["", "0/second", "5/week", "5 / minute", "-1/second", "٣/second", "5/minute\n"],
)
def test_rate_limit_rejects_malformed_rules(rule: str) -> None:
    with pytest.raises(ValueError):
        rate_limit(rule)


@pytest.mark.parametrize("max_keys", [0, -1, True])
def test_rate_limit_rejects_invalid_max_keys(max_keys: int) -> None:
    with pytest.raises(ValueError):
        rate_limit("1/second", max_keys=max_keys)
