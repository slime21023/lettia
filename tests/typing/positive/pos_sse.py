"""A typed event stream: the item type is fixed by ``EventStream[Item]``."""

from collections.abc import AsyncIterator
from typing import Protocol, assert_type, override

import msgspec

from lettia import Context, op, public
from lettia.core import Response
from lettia.extensions.sse import EventStream, ServerSentEvent, event_stream


class Tick(msgspec.Struct):
    n: int


async def ticks() -> AsyncIterator[ServerSentEvent[Tick]]:
    yield ServerSentEvent(Tick(1), event="tick", id="1", retry=1000)


class Api(Protocol):
    @op.get("/ticks", guard=public)
    async def ticks(self, c: Context[None], req: None) -> EventStream[Tick]: ...


class Impl(Api):
    @override
    async def ticks(self, c: Context[None], req: None) -> EventStream[Tick]:
        return EventStream[Tick](ticks())


def core_helper() -> Response:
    return event_stream(ticks(), heartbeat_sec=None)


def item_type() -> None:
    reply = EventStream[Tick](ticks())
    assert_type(reply.body, AsyncIterator[ServerSentEvent[Tick]])


Impl()
