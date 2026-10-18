"""``TestClient``: an httpx client that talks to an app and runs its lifespan."""

import asyncio
import contextlib
import urllib.parse
from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any, Self, cast, overload, override

import httpx
import msgspec

from lettia.core import App, Message, WebSocketDisconnect


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


class WebSocketTestSession:
    """In-memory test session driving the ASGI WebSocket protocol."""

    def __init__(
        self,
        app: App,
        path: str,
        *,
        headers: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
        subprotocols: Sequence[str] = (),
    ) -> None:
        self._app = app
        self._path = path
        self._headers = headers
        self._subprotocols = list(subprotocols)
        self._to_app: asyncio.Queue[Message] = asyncio.Queue()
        self._from_app: asyncio.Queue[Message] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self.accepted: bool = False
        self.accepted_subprotocol: str | None = None
        self.close_code: int | None = None
        self.close_reason: str = ""
        self.response_status: int | None = None
        self.response_headers: list[tuple[bytes, bytes]] = []
        self.response_body: bytes = b""

    async def __aenter__(self) -> Self:
        parsed = urllib.parse.urlsplit(self._path)
        path = parsed.path or "/"
        query_string = parsed.query.encode("ascii")

        raw_headers: list[tuple[bytes, bytes]] = []
        if self._headers is not None:
            header_items = (
                self._headers.items()
                if isinstance(self._headers, Mapping)
                else self._headers
            )
            for k, v in header_items:
                raw_headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))

        scope: dict[str, Any] = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_string,
            "headers": raw_headers,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "subprotocols": self._subprotocols,
            "extensions": {"websocket.http.response": {}},
        }

        self._task = asyncio.create_task(
            self._app(scope, self._to_app.get, self._from_app.put)
        )
        await self._to_app.put({"type": "websocket.connect"})

        message = await self._next_from_app()
        message_type = message.get("type")
        if message_type == "websocket.accept":
            self.accepted = True
            self.accepted_subprotocol = message.get("subprotocol")
        elif message_type == "websocket.close":
            self.accepted = False
            raw_code = message.get("code")
            self.close_code = 1000 if raw_code is None else cast(int, raw_code)
            self.close_reason = message.get("reason", "")
        elif message_type == "websocket.http.response.start":
            self.accepted = False
            self.response_status = message.get("status")
            self.response_headers = list(message.get("headers", ()))
            body_message = await self._next_from_app()
            self.response_body = body_message.get("body", b"")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        if self.accepted and self.close_code is None:
            with contextlib.suppress(Exception):
                await self.close()
        if self._task is not None and not self._task.done():
            await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
            try:
                await asyncio.wait_for(self._task, timeout=1.0)
            except (TimeoutError, Exception):
                self._task.cancel()

    async def _next_from_app(self) -> Message:
        task = self._task
        if task is None:
            raise RuntimeError("WebSocket session was not started")
        reply = asyncio.create_task(self._from_app.get())
        done, _ = await asyncio.wait({reply, task}, return_when=asyncio.FIRST_COMPLETED)
        if reply in done:
            return reply.result()
        reply.cancel()
        task.result()
        raise RuntimeError("The application ended without answering the WebSocket")

    async def send_text(self, text: str) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": text})

    async def send_bytes(self, data: bytes) -> None:
        await self._to_app.put({"type": "websocket.receive", "bytes": data})

    async def send_json(self, payload: object) -> None:
        encoded = msgspec.json.encode(payload).decode("utf-8")
        await self.send_text(encoded)

    async def receive_text(self) -> str:
        message = await self._next_from_app()
        if message.get("type") == "websocket.close":
            raw_code = message.get("code")
            self.close_code = 1000 if raw_code is None else cast(int, raw_code)
            self.close_reason = message.get("reason", "")
            raise WebSocketDisconnect(self.close_code, self.close_reason)
        return cast(str, message["text"])

    async def receive_bytes(self) -> bytes:
        message = await self._next_from_app()
        if message.get("type") == "websocket.close":
            raw_code = message.get("code")
            self.close_code = 1000 if raw_code is None else cast(int, raw_code)
            self.close_reason = message.get("reason", "")
            raise WebSocketDisconnect(self.close_code, self.close_reason)
        return cast(bytes, message["bytes"])

    @overload
    async def receive_json(self) -> Any: ...

    @overload
    async def receive_json[T](self, type_spec: type[T]) -> T: ...

    async def receive_json[T](self, type_spec: type[T] | None = None) -> Any:
        message = await self._next_from_app()
        if message.get("type") == "websocket.close":
            raw_code = message.get("code")
            self.close_code = 1000 if raw_code is None else cast(int, raw_code)
            self.close_reason = message.get("reason", "")
            raise WebSocketDisconnect(self.close_code, self.close_reason)
        text = message.get("text")
        if text is not None:
            raw = text.encode("utf-8")
        else:
            raw = cast(bytes, message["bytes"])
        if type_spec is None:
            return msgspec.json.decode(raw)
        return msgspec.json.decode(raw, type=type_spec)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_code = code
        self.close_reason = reason
        await self._to_app.put(
            {"type": "websocket.disconnect", "code": code, "reason": reason}
        )


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

    def websocket_connect(
        self,
        path: str,
        *,
        headers: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
        subprotocols: Sequence[str] = (),
    ) -> WebSocketTestSession:
        """Open an in-memory WebSocket connection to the tested app."""
        return WebSocketTestSession(
            self._app, path, headers=headers, subprotocols=subprotocols
        )

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
