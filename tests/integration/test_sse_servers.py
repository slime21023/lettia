"""Server-Sent Events served by real ASGI servers.

What differs between servers is how they report a client that went away and how
much they buffer for a slow client, so the same checks run against each of them.
"""

import json
import time
from collections.abc import Callable, Iterator
from itertools import islice
from typing import Any

import httpx
import pytest

from tests.integration.server_harness import SERVERS, serve

pytestmark = pytest.mark.server

APP = "tests.integration.sse_app:app"
MEMBER = {"authorization": "Bearer member-pass"}


@pytest.fixture(scope="module", params=SERVERS)
def base_url(request: pytest.FixtureRequest) -> Iterator[str]:
    with serve(request.param, APP, "/healthz") as url:
        yield url


@pytest.fixture
def http(base_url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=base_url, timeout=10) as client:
        yield client


def state(http: httpx.Client) -> dict[str, Any]:
    return http.get("/state").json()


def wait_for(condition: Callable[[], bool], seconds: float = 8) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def events(lines: Iterator[str]) -> Iterator[dict[str, str]]:
    """Group SSE lines into events; comments arrive as ``{": ": text}``."""
    current: dict[str, str] = {}
    for line in lines:
        if line == "":
            if current:
                yield current
            current = {}
        elif line.startswith(":"):
            yield {": ": line[1:].strip()}
        else:
            name, _, value = line.partition(": ")
            current[name] = value
    if current:
        yield current


# --- delivery -----------------------------------------------------------


def test_headers_and_the_connected_comment_arrive_before_any_event(
    http: httpx.Client,
) -> None:
    with http.stream("GET", "/endless") as res:
        first = next(events(res.iter_lines()))

        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        assert res.headers["cache-control"] == "no-cache"
        assert first == {": ": "connected"}


def test_events_arrive_incrementally_with_json_data(http: httpx.Client) -> None:
    with http.stream("GET", "/endless") as res:
        data_events = (e for e in events(res.iter_lines()) if "data" in e)
        seen = list(islice(data_events, 3))

    assert [json.loads(e["data"]) for e in seen] == [{"n": 1}, {"n": 2}, {"n": 3}]
    assert [(e["event"], e["id"]) for e in seen] == [
        ("tick", "1"),
        ("tick", "2"),
        ("tick", "3"),
    ]


def test_heartbeats_fill_the_gaps_and_the_next_event_still_arrives(
    http: httpx.Client,
) -> None:
    with http.stream("GET", "/gappy") as res:
        received = [
            "data" if "data" in e else e[": "] for e in events(res.iter_lines())
        ]

    assert received[0] == "connected"
    first, second = (i for i, kind in enumerate(received) if kind == "data")
    assert set(received[first + 1 : second]) == {"keep-alive"}
    assert second - first >= 2


def test_a_finite_stream_ends_and_head_has_no_body(http: httpx.Client) -> None:
    res = http.get("/finite", params={"count": 3})
    head = http.head("/finite", params={"count": 3})

    data = [
        json.loads(e["data"])
        for e in events(iter(res.text.splitlines()))
        if "data" in e
    ]
    assert data == [{"n": 1}, {"n": 2}, {"n": 3}]
    assert (head.status_code, head.content) == (200, b"")
    assert head.headers["content-type"].startswith("text/event-stream")


# --- refusal before the stream starts -----------------------------------


def test_guards_and_decoding_answer_before_a_stream_starts(http: httpx.Client) -> None:
    missing = http.get("/private")
    wrong = http.get("/private", headers={"authorization": "Bearer nope"})
    invalid = http.get("/finite", params={"count": "many"})
    allowed = http.get("/private", headers=MEMBER, params={"count": 1})

    assert (missing.status_code, wrong.status_code, invalid.status_code) == (
        401,
        403,
        422,
    )
    for refused in (missing, wrong, invalid):
        assert refused.headers["content-type"] == "application/problem+json"
    assert allowed.status_code == 200
    assert 'data: {"n":1}' in allowed.text


# --- cleanup and flow control -------------------------------------------


def test_a_client_that_leaves_closes_the_source_and_leaves_no_tasks(
    http: httpx.Client,
) -> None:
    before = state(http)

    with http.stream("GET", "/endless") as res:
        assert any("data" in e for e in events(res.iter_lines()))

    assert wait_for(lambda: state(http)["closed"] == before["closed"] + 1)
    assert wait_for(lambda: state(http)["stray_tasks"] == 0)
    assert state(http)["started"] == before["started"] + 1


def test_a_client_that_leaves_an_idle_stream_closes_the_source(
    http: httpx.Client,
) -> None:
    before = state(http)

    with http.stream("GET", "/idle") as res:
        assert any("data" in e for e in events(res.iter_lines()))

    assert wait_for(lambda: state(http)["closed"] == before["closed"] + 1)
    assert wait_for(lambda: state(http)["stray_tasks"] == 0)


def test_a_slow_client_stalls_the_producer_instead_of_buffering_without_bound(
    http: httpx.Client,
) -> None:
    before = state(http)

    with http.stream("GET", "/flood") as res:
        chunks = res.iter_bytes(chunk_size=1024)
        next(chunks)
        time.sleep(1.5)
        first = state(http)["flooded"]
        time.sleep(1.5)
        second = state(http)["flooded"]

    assert second - first <= 2, "the producer kept running ahead of a stalled client"
    assert second < 2000
    assert wait_for(lambda: state(http)["closed"] == before["closed"] + 1)
    assert wait_for(lambda: state(http)["stray_tasks"] == 0)
