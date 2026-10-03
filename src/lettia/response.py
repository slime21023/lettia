import asyncio
import json
import logging
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Mapping

from attrs import define, field

from lettia._headers import (
    build_headers,
    cookie_value,
    header_keys,
    validate_header_component,
)
from lettia._json import validate_json_value
from lettia.asgi import HTTPSend, JSONDocument, JSONValue

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
        validate_header_component(name, "name")
        validate_header_component(value, "value")
        for key in header_keys(self.headers, name):
            del self.headers[key]
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
        cookie_val = cookie_value(
            key, value, max_age, path, domain, secure, httponly, samesite, expires
        )

        # Store cookies as multi-headers
        # In ASGI, set-cookie headers can be sent repeatedly
        cookie_keys = header_keys(self.headers, "set-cookie")
        cookie_values = [self.headers[name] for name in cookie_keys]
        for name in cookie_keys:
            del self.headers[name]
        self.headers["set-cookie"] = "\n".join([*cookie_values, cookie_val])

    def delete_cookie(
        self,
        key: str,
        path: str = "/",
        domain: str | None = None,
        *,
        secure: bool = False,
        httponly: bool = False,
        samesite: str = "lax",
    ) -> None:
        self.set_cookie(
            key,
            "",
            max_age=0,
            path=path,
            domain=domain,
            secure=secure or key.lower().startswith(("__host-", "__secure-")),
            httponly=httponly,
            samesite=samesite,
        )


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
    _start_attempted: bool = field(default=False, init=False)
    _start_ready: asyncio.Event = field(factory=asyncio.Event, init=False)
    _can_finish: bool = field(default=False, init=False)
    _body_complete: bool = field(default=False, init=False)
    _closing: bool = field(default=False, init=False)
    _cleanup_failed: bool = field(default=False, init=False)
    _write_lock: asyncio.Lock = field(factory=asyncio.Lock, init=False)
    _finalize_response: Callable[[Response], None] | None = field(
        default=None, init=False
    )

    @property
    def _can_replace_response(self) -> bool:
        return not self._start_attempted

    async def write(self, response: Response, deadline: float | None = None) -> None:
        await self._deliver(response, deadline, None)

    async def _deliver(
        self,
        response: Response,
        deadline: float | None,
        finalizer: Callable[[Response], None] | None,
        wait_for_disconnect: Callable[[], Awaitable[None]] | None = None,
    ) -> bool:
        """Serialize delivery and return eligibility for completion work.

        A rejected pre-start response can be replaced. Once send is attempted,
        neither public writes nor application replacements may start again.
        """
        async with self._write_lock:
            if self._start_attempted:
                return False
            self._finalize_response = finalizer
            try:
                return await self._coordinate(response, deadline, wait_for_disconnect)
            finally:
                self._finalize_response = None

    async def _coordinate(
        self,
        response: Response,
        deadline: float | None,
        wait_for_disconnect: Callable[[], Awaitable[None]] | None,
    ) -> bool:
        if (
            wait_for_disconnect is None
            or response.async_body is None
            or self.head_only
            or response.status_code in (204, 304)
        ):
            await self._write_serial(response, deadline=deadline)
            return not self._cleanup_failed

        async def monitor_disconnect() -> None:
            # A response rejected before send must leave the request available to
            # the error handler. Start listening only after validation succeeds.
            await self._start_ready.wait()
            await wait_for_disconnect()

        writing = asyncio.create_task(self._write_serial(response, deadline=deadline))
        disconnected = asyncio.create_task(monitor_disconnect())
        try:
            done, _ = await asyncio.wait(
                (writing, disconnected), return_when=asyncio.FIRST_COMPLETED
            )
            if writing in done:
                await writing
                return not self._cleanup_failed
            await disconnected
            # ASGI also reports disconnect after a successful final body send.
            # Cleanup belongs to the writer and must not be cancelled by that.
            if self._closing:
                await asyncio.shield(writing)
                return self._body_complete and not self._cleanup_failed
            return False
        finally:
            if not disconnected.done():
                disconnected.cancel()
            if not writing.done() and not self._closing:
                # A deadline may already be cancelling the source inside anext().
                # Preserve its cleanup, but do not send a timeout end afterwards.
                self._can_finish = False
                if not writing.cancelling():
                    writing.cancel()
            await _await_cleanup(
                asyncio.gather(writing, disconnected, return_exceptions=True)
            )

    async def _write_serial(self, response: Response, deadline: float | None) -> None:
        self._closing = False
        if response.async_body is None:
            await self._write_owned(response, deadline)
            return

        started = False
        cancelled = False

        async def write_and_close() -> None:
            nonlocal started
            started = True
            await self._write_owned(response, deadline, cancelled=cancelled)

        # The same task must acquire, iterate and close the stream. Shield
        # the owner, rather than moving aclose() to a different task/Context.
        writing = asyncio.create_task(write_and_close())
        try:
            await asyncio.shield(writing)
        except asyncio.CancelledError:
            cancelled = True
            self._can_finish = False
            if started and not self._closing and not writing.cancelling():
                writing.cancel()
            try:
                await _await_cleanup(asyncio.gather(writing, return_exceptions=True))
            finally:
                raise

    async def _write_owned(
        self, response: Response, deadline: float | None, *, cancelled: bool = False
    ) -> None:
        stream = aiter(response.async_body) if response.async_body is not None else None
        try:
            if cancelled:
                raise asyncio.CancelledError
            if deadline is None:
                await self._write(response, stream)
                return
            deadline_scope = asyncio.timeout_at(deadline)
            try:
                async with deadline_scope:
                    await self._write(response, stream)
            except TimeoutError as exc:
                if not deadline_scope.expired():
                    raise
                if not self._start_attempted:
                    raise ResponseTimeout from exc
                if not self._can_finish:
                    raise
                logger.warning("Response deadline expired after headers were sent")
                await self._send_stream_end()
        finally:
            self._closing = True
            if stream is not None:
                try:
                    await _close_async_iterable(stream)
                except (Exception, asyncio.CancelledError):
                    self._cleanup_failed = True
                    raise

    async def _write(
        self, response: Response, stream: AsyncIterator[bytes] | None
    ) -> None:
        if self._finalize_response is not None:
            # Reused responses and failed attempts must not retain policy cookies.
            response = Response(
                status_code=response.status_code,
                headers=response.headers.copy(),
                body=response.body,
                media_type=response.media_type,
                async_body=response.async_body,
            )
            self._finalize_response(response)
        raw_headers = build_headers(
            response.headers,
            response.media_type,
            response.status_code,
            len(response.body),
            response.async_body is not None,
            self.head_only,
        )
        self._start_ready.set()
        self._start_attempted = True
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
            self._body_complete = True

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
        self._body_complete = True


async def _close_async_iterable(stream: AsyncIterable[bytes]) -> None:
    aclose = getattr(stream, "aclose", None)
    if aclose is not None:
        await aclose()


async def _await_cleanup[T](task: asyncio.Future[T]) -> T:
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break  # Re-raise below after resolving any external cancellation.
    if cancelled:
        if not task.cancelled():
            task.exception()  # Retrieve a cleanup failure before propagating cancel.
        raise asyncio.CancelledError
    return task.result()


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
