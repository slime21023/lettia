import asyncio
import dataclasses
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, NoReturn, TypeVar, cast
from urllib.parse import parse_qs

from attrs import define, field, has

from lettia._json import validate_json_value as validate_json_value
from lettia.asgi import HTTPReceive, HTTPRequestEvent, HTTPScope, HTTPSend, JSONValue
from lettia.errors import HTTPException, abort
from lettia.state import StateStore

if TYPE_CHECKING:
    from lettia.response import Response

T = TypeVar("T")
DefaultT = TypeVar("DefaultT")


@define(slots=True)
class Context:
    scope: HTTPScope
    receive: HTTPReceive
    send: HTTPSend
    path_params: dict[str, str] = field(factory=dict[str, str])
    state: StateStore = field(factory=StateStore)

    # Lazy-parsing caches
    _query_params: dict[str, list[str]] | None = field(default=None, init=False)
    _headers: dict[str, str] | None = field(default=None, init=False)
    _cookies: dict[str, str] | None = field(default=None, init=False)
    _body: bytes | None = field(default=None, init=False)
    _body_lock: asyncio.Lock = field(factory=asyncio.Lock, init=False)
    _body_limit: int | None = field(default=None, init=False)
    _body_error: HTTPException | None = field(default=None, init=False)
    _body_chunks: list[bytes] = field(factory=list[bytes], init=False)
    _body_received: int = field(default=0, init=False)
    _disconnected: bool = field(default=False, init=False)
    _response_deadline: float | None = field(default=None, init=False)
    _response_finalizers: list[Callable[["Response"], None]] | None = field(
        default=None, init=False
    )

    # Background task list
    _background_tasks: list[
        tuple[Callable[..., object], tuple[object, ...], dict[str, object]]
    ] = field(
        factory=list[
            tuple[Callable[..., object], tuple[object, ...], dict[str, object]]
        ],
        init=False,
    )

    @property
    def path(self) -> str:
        return self.scope.get("path", "/")

    @path.setter
    def path(self, value: str) -> None:
        self.scope["path"] = value

    @property
    def method(self) -> str:
        return self.scope.get("method", "GET").upper()

    @property
    def query_params(self) -> dict[str, list[str]]:
        if self._query_params is None:
            raw_query_string: object = dict(self.scope).get("query_string", b"")
            if type(raw_query_string) is not bytes:
                abort(400, "Invalid query string")
            raw_qs = raw_query_string.decode("latin-1")
            self._query_params = parse_qs(raw_qs, keep_blank_values=True)
        return self._query_params

    def query_param(
        self, key: str, default: DefaultT | None = None
    ) -> str | DefaultT | None:
        values = self.query_params.get(key)
        if values and len(values) > 0:
            return values[0]
        return default

    @property
    def headers(self) -> dict[str, str]:
        if self._headers is None:
            hdr_dict: dict[str, str] = {}
            raw_headers = self.scope.get("headers", [])
            for k, v in raw_headers:
                name = k.decode("latin-1").lower()
                val = v.decode("latin-1")
                if name in hdr_dict:
                    separator = "; " if name == "cookie" else ", "
                    hdr_dict[name] += f"{separator}{val}"
                else:
                    hdr_dict[name] = val
            self._headers = hdr_dict
        return self._headers

    def header(
        self, key: str, default: DefaultT | None = None
    ) -> str | DefaultT | None:
        return self.headers.get(key.lower(), default)

    @property
    def cookies(self) -> dict[str, str]:
        if self._cookies is None:
            cookie_hdr = self.header("cookie", "")
            cookie_dict: dict[str, str] = {}
            if cookie_hdr:
                for item in cookie_hdr.split(";"):
                    if "=" in item:
                        k, v = item.strip().split("=", 1)
                        cookie_dict[k] = v
            self._cookies = cookie_dict
        return self._cookies

    def cookie(
        self, key: str, default: DefaultT | None = None
    ) -> str | DefaultT | None:
        return self.cookies.get(key, default)

    async def body(self, max_bytes: int | None = None) -> bytes:
        if max_bytes is not None:
            if self._body_limit is None or max_bytes < self._body_limit:
                self._body_limit = max_bytes
        async with self._body_lock:
            if self._body_error is not None:
                raise HTTPException(
                    self._body_error.status_code,
                    self._body_error.detail,
                    self._body_error.headers,
                )
            try:
                self._check_body_limit()
                return await self._read_body()
            except HTTPException as exc:
                # Keep the rejection, not a traceback retaining request chunks.
                self._body_error = HTTPException(
                    exc.status_code, exc.detail, exc.headers
                )
                self._body = None
                self._body_chunks.clear()
                raise

    def _check_body_limit(self) -> None:
        if self._body_limit is None:
            return
        content_length = self.header("content-length")
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                abort(400, "Content-Length must be an integer")
            if length < 0:
                abort(400, "Content-Length must not be negative")
            if length > self._body_limit:
                abort(413, f"Content-Length exceeds limit of {self._body_limit} bytes")
        if self._body_received > self._body_limit:
            abort(
                413, f"Request payload size exceeds limit of {self._body_limit} bytes"
            )

    async def _read_body(self) -> bytes:
        if self._body is not None:
            return self._body

        more_body = True

        while more_body:
            message = await self.receive()
            if message["type"] == "http.disconnect":
                self._disconnected = True
                abort(400, "Client disconnected while reading request body")
            if message["type"] != "http.request":
                abort(400, f"Unexpected ASGI message: {message['type']!r}")

            chunk = _body_chunk(message)
            self._body_received += len(chunk)
            self._check_body_limit()
            self._body_chunks.append(chunk)
            more_body = message.get("more_body", False)

        self._body = b"".join(self._body_chunks)
        self._body_chunks.clear()
        return self._body

    async def _wait_for_disconnect(self) -> None:
        # Share the body cache with streams that read their request lazily.
        # Only one reader may own the ASGI receive channel at a time.
        try:
            await self.body()
        except HTTPException:
            if self._disconnected:
                return
            # Rejected input must never be cached or made readable again.
            # Drain subsequent ASGI events without retaining their bodies so
            # disconnect monitoring still works for streamed error responses.
        while not self._disconnected:
            message = await self.receive()
            self._disconnected = message["type"] == "http.disconnect"

    def _defer_response_policies(self) -> None:
        if self._response_finalizers is None:
            self._response_finalizers = []

    def _register_response_finalizer(
        self, response: "Response", finalize: Callable[["Response"], None]
    ) -> None:
        if self._response_finalizers is None:
            # Standalone middleware still returns a fully decorated response.
            finalize(response)
        else:
            self._response_finalizers.append(finalize)

    def _finalize_response(self, response: "Response") -> None:
        if self._response_finalizers is None:
            return
        failure: Exception | None = None
        for finalize in self._response_finalizers.copy():
            try:
                finalize(response)
            except Exception as exc:
                # A broken policy must not also prevent the fallback response.
                self._response_finalizers.remove(finalize)
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure

    async def json(self) -> JSONValue:
        body_data = await self.body()
        if not body_data:
            return None
        try:
            decoded: object = json.loads(body_data.decode("utf-8"))
            return validate_json_value(decoded)
        except RecursionError:
            abort(400, "JSON request body exceeds maximum nesting depth")
        except ValueError as exc:
            abort(400, f"Invalid JSON request body: {exc}")

    async def text(self) -> str:
        body_data = await self.body()
        try:
            return body_data.decode("utf-8")
        except UnicodeDecodeError as exc:
            abort(400, f"Request body is not valid UTF-8: {exc}")

    def add_background_task(
        self, func: Callable[..., object], *args: object, **kwargs: object
    ) -> None:
        self._background_tasks.append((func, args, kwargs))

    @property
    def response_deadline(self) -> float | None:
        return self._response_deadline

    @response_deadline.setter
    def response_deadline(self, value: float | None) -> None:
        self._response_deadline = value

    @property
    def background_tasks(
        self,
    ) -> list[tuple[Callable[..., object], tuple[object, ...], dict[str, object]]]:
        return self._background_tasks

    async def bind(self, target_type: type[T]) -> T:
        from lettia.protocols.binder import (
            AttrsBinder,
            DataclassBinder,
            PydanticBinder,
        )

        if has(target_type):
            # attrs.has() narrows to AttrsInstance, losing the caller's T in Pyrefly.
            return cast(T, await AttrsBinder().bind(self, target_type))
        if dataclasses.is_dataclass(target_type):
            # Preserve T across ty's dataclass protocol narrowing.
            return cast(T, await DataclassBinder().bind(self, target_type))

        try:
            import pydantic  # pyright: ignore[reportMissingImports]
        except ImportError:
            pass
        else:
            if issubclass(target_type, pydantic.BaseModel):
                # The binder constructs target_type, preserving the caller's T.
                return cast(T, await PydanticBinder().bind(self, target_type))

        # Preserve the unsupported-target TypeError from AttrsBinder.
        return await AttrsBinder().bind(self, target_type)

    def abort(
        self,
        status_code: int,
        detail: object = None,
        headers: dict[str, str] | None = None,
    ) -> NoReturn:
        abort(status_code, detail, headers)


def _body_chunk(message: HTTPRequestEvent) -> bytes:
    value: object = message["body"] if "body" in message else b""
    if type(value) is not bytes:
        abort(400, "ASGI request body must be bytes")
    return value
