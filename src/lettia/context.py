import dataclasses
import json
from collections.abc import Callable
from typing import Any, NoReturn, TypeVar
from urllib.parse import parse_qs

from attrs import define, field, has

from lettia.errors import abort

T = TypeVar("T")


@define(slots=True)
class Context:
    scope: dict[str, Any]
    receive: Any
    send: Any
    path_params: dict[str, str] = field(factory=dict)
    state: dict[str, Any] = field(factory=dict)

    # Lazy-parsing caches
    _query_params: dict[str, list[str]] | None = field(default=None, init=False)
    _headers: dict[str, str] | None = field(default=None, init=False)
    _cookies: dict[str, str] | None = field(default=None, init=False)
    _body: bytes | None = field(default=None, init=False)

    # Background task list
    _background_tasks: list[
        tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]
    ] = field(factory=list, init=False)

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
            raw_qs = self.scope.get("query_string", b"")
            if isinstance(raw_qs, bytes):
                raw_qs = raw_qs.decode("latin-1")
            self._query_params = parse_qs(raw_qs, keep_blank_values=True)
        return self._query_params

    def query_param(self, key: str, default: Any = None) -> str | Any:
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
                    hdr_dict[name] += f", {val}"
                else:
                    hdr_dict[name] = val
            self._headers = hdr_dict
        return self._headers

    def header(self, key: str, default: Any = None) -> str | Any:
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

    def cookie(self, key: str, default: Any = None) -> str | Any:
        return self.cookies.get(key, default)

    async def body(self, max_bytes: int | None = None) -> bytes:
        if self._body is not None:
            return self._body

        chunks: list[bytes] = []
        bytes_received = 0
        more_body = True

        while more_body:
            message = await self.receive()
            message_type = message.get("type")
            if message_type == "http.disconnect":
                abort(400, "Client disconnected while reading request body")
            if message_type != "http.request":
                abort(400, f"Unexpected ASGI message: {message_type!r}")

            chunk = message.get("body", b"")
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

    async def json(self) -> Any:
        body_data = await self.body()
        if not body_data:
            return None
        try:
            return json.loads(body_data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            abort(400, f"Invalid JSON request body: {exc}")

    async def text(self) -> str:
        body_data = await self.body()
        try:
            return body_data.decode("utf-8")
        except UnicodeDecodeError as exc:
            abort(400, f"Request body is not valid UTF-8: {exc}")

    def add_background_task(
        self, func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> None:
        self._background_tasks.append((func, args, kwargs))

    async def bind(self, target_type: type[T]) -> T:
        from lettia.protocols.binder import (
            AttrsBinder,
            DataclassBinder,
            PydanticBinder,
        )

        if has(target_type):
            return await AttrsBinder().bind(self, target_type)
        if dataclasses.is_dataclass(target_type):
            return await DataclassBinder().bind(self, target_type)

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
        detail: Any = None,
        headers: dict[str, str] | None = None,
    ) -> NoReturn:
        abort(status_code, detail, headers)
