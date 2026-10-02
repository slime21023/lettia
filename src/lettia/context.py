import dataclasses
import json
from collections.abc import Callable
from typing import NoReturn, TypeVar, cast
from urllib.parse import parse_qs

from attrs import define, field, has

from lettia.asgi import HTTPReceive, HTTPRequestEvent, HTTPScope, HTTPSend, JSONValue
from lettia.errors import abort
from lettia.state import StateStore

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
    _response_deadline: float | None = field(default=None, init=False)

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
        if self._body is not None:
            if max_bytes is not None and len(self._body) > max_bytes:
                abort(413, f"Request payload size exceeds limit of {max_bytes} bytes")
            return self._body

        chunks: list[bytes] = []
        bytes_received = 0
        more_body = True

        while more_body:
            message = await self.receive()
            if message["type"] == "http.disconnect":
                abort(400, "Client disconnected while reading request body")
            if message["type"] != "http.request":
                abort(400, f"Unexpected ASGI message: {message['type']!r}")

            chunk = _body_chunk(message)
            bytes_received += len(chunk)
            if max_bytes is not None and bytes_received > max_bytes:
                abort(
                    413,
                    f"Request payload size exceeds limit of {max_bytes} bytes",
                )
            chunks.append(chunk)
            more_body = message.get("more_body", False)

        self._body = b"".join(chunks)
        return self._body

    async def json(self) -> JSONValue:
        body_data = await self.body()
        if not body_data:
            return None
        try:
            decoded: object = json.loads(body_data.decode("utf-8"))
            return validate_json_value(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
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
            return cast(T, await AttrsBinder().bind(self, target_type))
        if dataclasses.is_dataclass(target_type):
            return cast(T, await DataclassBinder().bind(self, target_type))

        # Check for Pydantic BaseModel or fallback
        try:
            import pydantic  # pyright: ignore[reportMissingImports]

            if issubclass(target_type, pydantic.BaseModel):
                return await PydanticBinder().bind(self, target_type)
        except (ImportError, TypeError):
            pass

        # Default fallback to AttrsBinder or raise ValueError
        return await AttrsBinder().bind(self, target_type)

    def abort(
        self,
        status_code: int,
        detail: object = None,
        headers: dict[str, str] | None = None,
    ) -> NoReturn:
        abort(status_code, detail, headers)


def validate_json_value(value: object) -> JSONValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        items = cast(list[object], value)
        return [validate_json_value(item) for item in items]
    if isinstance(value, dict):
        converted: dict[str, JSONValue] = {}
        items = cast(dict[object, object], value)
        for key, item in items.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            converted[key] = validate_json_value(item)
        return converted
    raise TypeError(f"Decoded JSON has unsupported type: {type(value).__name__}")


def _body_chunk(message: HTTPRequestEvent) -> bytes:
    value: object = message["body"] if "body" in message else b""
    if type(value) is not bytes:
        abort(400, "ASGI request body must be bytes")
    return value
