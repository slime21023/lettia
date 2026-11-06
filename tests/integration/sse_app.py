"""The probe app behind ``test_sse_servers``: streams whose cleanup is observable.

``/state`` reports how often each source started and was closed, how many events
a flooding producer created, and how many tasks of the SSE machinery are alive.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol, override

import msgspec

from lettia import (
    App,
    Context,
    GuardSpec,
    HttpBearer,
    Ok,
    Query,
    RequestHead,
    op,
    public,
)
from lettia.core import Forbidden, Unauthorized
from lettia.extensions.sse import EventStream, ServerSentEvent

PAYLOAD = "x" * 262_144


class Tick(msgspec.Struct):
    n: int


class Blob(msgspec.Struct):
    n: int
    payload: str


class State(msgspec.Struct):
    started: int
    closed: int
    flooded: int
    stray_tasks: int


class CountReq(msgspec.Struct):
    count: Query[int] = 3


class Member(msgspec.Struct):
    name: str


member = GuardSpec[Member]("member", scheme=HttpBearer())


class FastBeat[T](EventStream[T]):
    heartbeat_sec = 0.2


class ProbeApi(Protocol):
    @op.get("/healthz", guard=public)
    async def health(self, c: Context[None], req: None) -> Ok[str]: ...

    @op.get("/state", guard=public)
    async def state(self, c: Context[None], req: None) -> Ok[State]: ...

    @op.get("/endless", guard=public)
    async def endless(self, c: Context[None], req: None) -> EventStream[Tick]: ...

    @op.get("/finite", guard=public)
    async def finite(self, c: Context[None], req: CountReq) -> EventStream[Tick]: ...

    @op.get("/idle", guard=public)
    async def idle(self, c: Context[None], req: None) -> FastBeat[Tick]: ...

    @op.get("/gappy", guard=public)
    async def gappy(self, c: Context[None], req: None) -> FastBeat[Tick]: ...

    @op.get("/flood", guard=public)
    async def flood(self, c: Context[None], req: None) -> EventStream[Blob]: ...

    @op.get("/private", guard=member)
    async def private(self, c: Context[Member], req: CountReq) -> EventStream[Tick]: ...


counters = {"started": 0, "closed": 0, "flooded": 0}


async def numbered(
    count: int | None, pause: float
) -> AsyncIterator[ServerSentEvent[Tick]]:
    counters["started"] += 1
    try:
        n = 0
        while count is None or n < count:
            n += 1
            yield ServerSentEvent(Tick(n), event="tick", id=str(n))
            await asyncio.sleep(pause)
    finally:
        counters["closed"] += 1


async def one_then_idle() -> AsyncIterator[ServerSentEvent[Tick]]:
    counters["started"] += 1
    try:
        yield ServerSentEvent(Tick(1))
        await asyncio.Event().wait()
    finally:
        counters["closed"] += 1


async def flooding() -> AsyncIterator[ServerSentEvent[Blob]]:
    counters["started"] += 1
    try:
        n = 0
        while True:
            n += 1
            counters["flooded"] = n
            yield ServerSentEvent(Blob(n, PAYLOAD))
            await asyncio.sleep(0)
    finally:
        counters["closed"] += 1


def stray_tasks() -> int:
    """Tasks the SSE machinery owns: response pumps, watchers, pending reads."""
    names = ("_send_monitored_response", "_wrap_awaitable")
    return sum(
        1
        for task in asyncio.all_tasks()
        if any(name in getattr(task.get_coro(), "__qualname__", "") for name in names)
    )


class ProbeService(ProbeApi):
    @override
    async def health(self, c: Context[None], req: None) -> Ok[str]:
        return Ok("ok")

    @override
    async def state(self, c: Context[None], req: None) -> Ok[State]:
        return Ok(State(stray_tasks=stray_tasks(), **counters))

    @override
    async def endless(self, c: Context[None], req: None) -> EventStream[Tick]:
        return EventStream[Tick](numbered(None, 0.05))

    @override
    async def finite(self, c: Context[None], req: CountReq) -> EventStream[Tick]:
        return EventStream[Tick](numbered(req.count, 0.01))

    @override
    async def idle(self, c: Context[None], req: None) -> FastBeat[Tick]:
        return FastBeat[Tick](one_then_idle())

    @override
    async def gappy(self, c: Context[None], req: None) -> FastBeat[Tick]:
        return FastBeat[Tick](numbered(2, 0.7))

    @override
    async def flood(self, c: Context[None], req: None) -> EventStream[Blob]:
        return EventStream[Blob](flooding())

    @override
    async def private(self, c: Context[Member], req: CountReq) -> EventStream[Tick]:
        return EventStream[Tick](numbered(req.count, 0.01))


async def verify_member(head: RequestHead) -> Member:
    scheme, _, credential = head.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not credential:
        raise Unauthorized("a bearer credential is required")
    if credential != "member-pass":
        raise Forbidden("members only")
    return Member("m")


app = App(title="SSE probe", guards=[member.use(verify_member)])
app.mount(ProbeService())
