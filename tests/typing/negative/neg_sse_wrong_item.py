# pyright: reportUnusedImport=false
from collections.abc import AsyncIterator

import msgspec

from lettia.extensions.sse import EventStream, ServerSentEvent


class Tick(msgspec.Struct):
    n: int


async def texts() -> AsyncIterator[ServerSentEvent[str]]:
    yield ServerSentEvent("not a tick")


reply = EventStream[Tick](texts())
