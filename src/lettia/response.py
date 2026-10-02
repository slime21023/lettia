import asyncio
import json
import logging
from collections.abc import AsyncIterable, AsyncIterator, Mapping

from attrs import define, field

from lettia.asgi import HTTPSend, JSONDocument, JSONValue
from lettia.context import validate_json_value

logger: logging.Logger = logging.getLogger("lettia.response")


class ResponseTimeout(Exception):
    """Raised when a response deadline expires before its headers are sent."""


@define(slots=True)
class Response:
    status_code: int = 200
    headers: dict[str, str] = field(factory=dict[str, str])
    body: bytes = b""
    media_type: str = "text/plain; charset=utf-8"
    async_body: AsyncIterable[bytes] | None = None

    def set_header(self, name: str, value: str) -> None:
        if "\r" in name or "\n" in name or "\r" in value or "\n" in value:
            raise ValueError("HTTP header names and values cannot contain newlines")
        self.headers[name.lower()] = value

    def set_cookie(
        self,
        key: str,
        value: str,
        max_age: int | None = None,
        path: str = "/",
        domain: str | None = None,
        secure: bool = False,
        httponly: bool = False,
        samesite: str = "lax",
        expires: str | None = None,
    ) -> None:
        if any("\r" in value or "\n" in value for value in (key, value, path)):
            raise ValueError("Cookie attributes cannot contain newlines")
        if domain is not None and ("\r" in domain or "\n" in domain):
            raise ValueError("Cookie attributes cannot contain newlines")
        if samesite and ("\r" in samesite or "\n" in samesite):
            raise ValueError("Cookie attributes cannot contain newlines")

        cookie_val = f"{key}={value}; Path={path}"
        if max_age is not None:
            cookie_val += f"; Max-Age={max_age}"
        if domain:
            cookie_val += f"; Domain={domain}"
        if expires:
            cookie_val += f"; Expires={expires}"
        if secure:
            cookie_val += "; Secure"
        if httponly:
            cookie_val += "; HttpOnly"
        if samesite:
            cookie_val += f"; SameSite={samesite}"

        # Store cookies as multi-headers
        # In ASGI, set-cookie headers can be sent repeatedly
        if "set-cookie" in self.headers:
            self.headers["set-cookie"] += f"\n{cookie_val}"
        else:
            self.headers["set-cookie"] = cookie_val

    def delete_cookie(
        self,
        key: str,
        path: str = "/",
        domain: str | None = None,
    ) -> None:
        self.set_cookie(key, "", max_age=0, path=path, domain=domain)


@define(slots=True)
class TextResponse(Response):
    def __init__(
        self,
        text: str,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        media_type: str = "text/plain; charset=utf-8",
    ) -> None:
        hdr = headers.copy() if headers else {}
        super().__init__(
            status_code=status_code,
            headers=hdr,
            body=text.encode("utf-8"),
            media_type=media_type,
        )


@define(slots=True)
class JsonResponse(Response):
    def __init__(
        self,
        data: JSONValue,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        media_type: str = "application/json",
    ) -> None:
        hdr = headers.copy() if headers else {}
        json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
        super().__init__(
            status_code=status_code,
            headers=hdr,
            body=json_bytes,
            media_type=media_type,
        )


@define(slots=True)
class StreamResponse(Response):
    def __init__(
        self,
        generator: AsyncIterable[bytes],
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        media_type: str = "application/octet-stream",
    ) -> None:
        hdr = headers.copy() if headers else {}
        super().__init__(
            status_code=status_code,
            headers=hdr,
            body=b"",
            media_type=media_type,
            async_body=generator,
        )


@define(slots=True)
class ResponseWriter:
    send: HTTPSend
    head_only: bool = False
    committed: bool = field(default=False, init=False)
    _can_finish: bool = field(default=False, init=False)

    async def write(self, response: Response, deadline: float | None = None) -> None:
        if self.committed:
            return

        if deadline is None:
            await self._write(response)
            return
        deadline_scope = asyncio.timeout_at(deadline)
        try:
            async with deadline_scope:
                await self._write(response)
        except TimeoutError as exc:
            if not deadline_scope.expired():
                raise
            if not self.committed:
                raise ResponseTimeout from exc
            if not self._can_finish:
                raise
            logger.warning("Response deadline expired after headers were sent")
            await self._send_stream_end()

    async def _write(self, response: Response) -> None:
        stream = aiter(response.async_body) if response.async_body is not None else None
        try:
            raw_headers = self._build_headers(response)
            await self.send(
                {
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": raw_headers,
                }
            )
            self.committed = True
            self._can_finish = True

            if self.head_only or response.status_code in (204, 304):
                await self._send_stream_end()
            elif stream is not None:
                await self._write_stream(stream)
            else:
                self._can_finish = False
                await self.send(
                    {
                        "type": "http.response.body",
                        "body": response.body,
                        "more_body": False,
                    }
                )
        finally:
            if stream is not None:
                await _close_async_iterable(stream)

    def _build_headers(self, response: Response) -> list[tuple[bytes, bytes]]:
        raw_headers: list[tuple[bytes, bytes]] = []
        has_content_type = False
        content_length: str | None = None

        for k, v in response.headers.items():
            self._validate_header_component(k, "name")
            k_bytes = k.lower().encode("latin-1")
            if k_bytes == b"content-type":
                has_content_type = True
            if k_bytes == b"content-length":
                if response.status_code in (204, 304):
                    continue
                if content_length is not None:
                    raise ValueError(
                        "Response cannot contain multiple Content-Length headers"
                    )
                content_length = v

            if k_bytes == b"set-cookie":
                for cookie_line in v.split("\n"):
                    self._validate_header_component(cookie_line, "value")
                    raw_headers.append((b"set-cookie", cookie_line.encode("latin-1")))
            else:
                self._validate_header_component(v, "value")
                raw_headers.append((k_bytes, v.encode("latin-1")))

        if not has_content_type and response.media_type:
            self._validate_header_component(response.media_type, "value")
            raw_headers.append((b"content-type", response.media_type.encode("latin-1")))

        if response.async_body is None and response.status_code not in (204, 304):
            expected_length = str(len(response.body))
            if content_length is not None and content_length != expected_length:
                raise ValueError("Content-Length does not match the response body")
            if content_length is None:
                raw_headers.append(
                    (b"content-length", expected_length.encode("latin-1"))
                )

        return raw_headers

    @staticmethod
    def _validate_header_component(value: str, component: str) -> None:
        if "\r" in value or "\n" in value:
            raise ValueError(f"HTTP header {component}s cannot contain newlines")
        try:
            value.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise ValueError(
                f"HTTP header {component}s must be Latin-1 encodable"
            ) from exc

    async def _write_stream(self, stream: AsyncIterator[bytes]) -> None:
        while True:
            try:
                chunk = await anext(stream)
            except StopAsyncIteration:
                break
            except Exception:
                await self._send_stream_end()
                raise
            try:
                await self.send(
                    {
                        "type": "http.response.body",
                        "body": chunk,
                        "more_body": True,
                    }
                )
            except (Exception, asyncio.CancelledError):
                self._can_finish = False
                raise
        await self._send_stream_end()

    async def _send_stream_end(self) -> None:
        self._can_finish = False
        await self.send(
            {
                "type": "http.response.body",
                "body": b"",
                "more_body": False,
            }
        )


async def _close_async_iterable(stream: AsyncIterable[bytes]) -> None:
    aclose = getattr(stream, "aclose", None)
    if aclose is not None:
        await aclose()


type ResponseBody = Response | str | bytes | JSONDocument
type ResponseValue = (
    ResponseBody
    | tuple[ResponseBody, int]
    | tuple[ResponseBody, int, Mapping[str, str]]
)


def normalize_response(result: ResponseValue) -> Response:
    if isinstance(result, Response):
        return result
    if isinstance(result, str):
        return TextResponse(result)
    if isinstance(result, bytes):
        return Response(body=result, media_type="application/octet-stream")
    if isinstance(result, Mapping):
        return JsonResponse(validate_json_value(dict(result)))
    if isinstance(result, list):
        return JsonResponse(validate_json_value(result))
    if len(result) in (2, 3):
        body_part, status_part = result[0], result[1]
        headers_part: Mapping[str, str] = result[2] if len(result) == 3 else {}
        resp = normalize_response(body_part)
        resp.status_code = status_part
        for k, v in headers_part.items():
            resp.set_header(k, v)
        return resp

    raise TypeError(f"Unsupported response value: {type(result).__name__}")
