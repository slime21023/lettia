"""A typed Server-Sent Events API.

Run it:

    uv run uvicorn examples.sse_api:app --host 127.0.0.1 --port 8765

Open http://127.0.0.1:8765/ in a browser (it uses EventSource), or:

    curl -N "localhost:8765/ticks?count=5&interval=0.5"
    curl -N -H "Authorization: Bearer demo" localhost:8765/private/ticks

Every event's `data` is JSON: a client reads it with `JSON.parse(event.data)`.
After a dropped connection EventSource reconnects with `Last-Event-ID`, and the
stream continues from the next number.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Protocol, override

import msgspec

from lettia import (
    App,
    Context,
    GuardSpec,
    Header,
    HeaderName,
    HttpBearer,
    Ok,
    Query,
    RequestHead,
    op,
    public,
)
from lettia.core import Forbidden, Unauthorized
from lettia.extensions.sse import EventStream, ServerSentEvent

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Lettia SSE</title>
<h1>Ticks</h1>
<pre id="log"></pre>
<script>
  const log = document.getElementById("log");
  const source = new EventSource("/ticks?count=20&interval=0.5");
  source.addEventListener("tick", (event) => {
    const tick = JSON.parse(event.data);
    log.textContent += `tick ${tick.n} at ${event.lastEventId}\\n`;
  });
  source.onerror = () => {
    log.textContent += "(closed or reconnecting)\\n";
    if (source.readyState === EventSource.CLOSED) source.close();
  };
</script>
"""


class Tick(msgspec.Struct):
    n: int


class TicksReq(msgspec.Struct):
    count: Query[int] = 10
    interval: Query[float] = 1.0
    last_event_id: Header[Annotated[str, HeaderName("Last-Event-ID")]] = ""


class PrivateReq(msgspec.Struct):
    count: Query[int] = 3


class Reader(msgspec.Struct):
    name: str


reader = GuardSpec[Reader]("reader", scheme=HttpBearer())


class Html(Ok[str]):
    media_type = "text/html; charset=utf-8"


class TicksApi(Protocol):
    @op.get("/", guard=public)
    async def page(self, c: Context[None], req: None) -> Html: ...

    @op.get("/ticks", guard=public)
    async def ticks(self, c: Context[None], req: TicksReq) -> EventStream[Tick]: ...

    @op.get("/private/ticks", guard=reader)
    async def private_ticks(
        self, c: Context[Reader], req: PrivateReq
    ) -> EventStream[Tick]: ...


async def ticks(
    first: int, count: int, interval: float
) -> AsyncIterator[ServerSentEvent[Tick]]:
    for n in range(first, count + 1):
        yield ServerSentEvent(Tick(n), event="tick", id=str(n), retry=2000)
        await asyncio.sleep(interval)


def resume_after(last_event_id: str) -> int:
    return int(last_event_id) + 1 if last_event_id.isdecimal() else 1


class TicksService(TicksApi):
    @override
    async def page(self, c: Context[None], req: None) -> Html:
        return Html(PAGE)

    @override
    async def ticks(self, c: Context[None], req: TicksReq) -> EventStream[Tick]:
        first = resume_after(req.last_event_id)
        return EventStream[Tick](ticks(first, req.count, max(req.interval, 0.01)))

    @override
    async def private_ticks(
        self, c: Context[Reader], req: PrivateReq
    ) -> EventStream[Tick]:
        return EventStream[Tick](ticks(1, req.count, 0.1))


READERS = frozenset({"demo"})


async def verify_reader(head: RequestHead) -> Reader:
    scheme, _, token = head.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("a bearer token is required")
    if token not in READERS:
        raise Forbidden("this token may not read ticks")
    return Reader(token)


app = App(
    title="SSE API",
    version="0.1.0",
    description="Typed Server-Sent Events example for lettia.",
    guards=[reader.use(verify_reader)],
)
app.mount(TicksService())
