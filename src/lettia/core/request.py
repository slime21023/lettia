"""The HTTP request: a lazily parsed head plus a single-use body."""

from collections.abc import AsyncIterator, Mapping
from urllib.parse import quote

from lettia.core.cookies import parse_cookies
from lettia.core.errors import HTTPError
from lettia.core.headers import Headers, RawHeaders
from lettia.core.query import QueryParams

DEFAULT_MAX_BODY_SIZE = 5 * 1024 * 1024

# Characters a path segment may contain unencoded (RFC 3986 pchar), plus "/".
_PATH_SAFE = "/:@!$&'()*+,;=-._~"


def _raw_path_from(path: str) -> bytes:
    """Rebuild a raw path from a decoded one, for servers that send no raw_path.

    The router splits the raw path and then percent-decodes each segment, so the
    decoded path must be encoded again; an encoded slash that the server already
    decoded cannot be told apart from a real one.
    """
    return quote(path, safe=_PATH_SAFE).encode("ascii")


class RequestHead:
    """Everything known about a request before its body is read.

    ``headers``, ``query`` and ``cookies`` are parsed on first access and cached,
    so a route that never looks at them does not pay for them. Guards receive the
    request typed as this class, which hides the body.
    """

    __slots__ = (
        "_cookies",
        "_headers",
        "_query",
        "_query_string",
        "_raw_headers",
        "client",
        "method",
        "path",
        "raw_path",
    )

    def __init__(
        self,
        *,
        method: str,
        path: str,
        raw_path: bytes | None = None,
        query_string: bytes = b"",
        headers: RawHeaders | None = None,
        client: tuple[str, int] | None = None,
    ) -> None:
        self.method = method
        self.path = path
        self.raw_path = _raw_path_from(path) if raw_path is None else raw_path
        self.client = client
        self._query_string = query_string
        self._raw_headers: RawHeaders = [] if headers is None else headers
        self._headers: Headers | None = None
        self._query: QueryParams | None = None
        self._cookies: dict[str, str] | None = None

    @property
    def loggable_path(self) -> str:
        """The path as it is safe to log, still percent-encoded.

        An encoded newline in the URL therefore cannot start a forged log line.
        """
        return self.raw_path.decode("latin-1")

    @property
    def headers(self) -> Headers:
        if self._headers is None:
            self._headers = Headers(self._raw_headers)
        return self._headers

    @property
    def query(self) -> QueryParams:
        if self._query is None:
            self._query = QueryParams.parse(self._query_string)
        return self._query

    @property
    def cookies(self) -> Mapping[str, str]:
        if self._cookies is None:
            self._cookies = parse_cookies(self.headers.get_all("cookie"))
        return self._cookies


class Request(RequestHead):
    """A request whose body is read either whole (``body``) or in chunks (``stream``).

    The two are mutually exclusive and the body can be consumed only once. The
    body comes from a plain async iterator, so tests can supply it without ASGI.
    A body larger than ``max_body_size`` raises ``HTTPError(413)``.
    """

    __slots__ = ("_cached", "_max_body_size", "_mode", "_source")

    def __init__(
        self,
        *,
        method: str,
        path: str,
        body: AsyncIterator[bytes],
        raw_path: bytes | None = None,
        query_string: bytes = b"",
        headers: RawHeaders | None = None,
        client: tuple[str, int] | None = None,
        max_body_size: int | None = DEFAULT_MAX_BODY_SIZE,
    ) -> None:
        super().__init__(
            method=method,
            path=path,
            raw_path=raw_path,
            query_string=query_string,
            headers=headers,
            client=client,
        )
        self._source = body
        self._max_body_size = max_body_size
        self._mode: str = "unread"
        self._cached: bytes | None = None

    async def body(self) -> bytes:
        """Read the whole body; later calls return the cached bytes."""
        if self._cached is not None:
            return self._cached
        if self._mode == "stream":
            raise RuntimeError("body() cannot be used after stream()")
        if self._mode == "body":
            raise RuntimeError("the body is already being read or failed to read")
        self._check_declared_length()
        self._mode = "body"
        self._cached = b"".join([chunk async for chunk in self._chunks()])
        return self._cached

    def stream(self) -> AsyncIterator[bytes]:
        """Iterate over the body in chunks, without holding it all in memory."""
        if self._mode == "body":
            raise RuntimeError("stream() cannot be used after body()")
        if self._mode == "stream":
            raise RuntimeError("stream() was already called")
        self._check_declared_length()
        self._mode = "stream"
        return self._chunks()

    def _check_declared_length(self) -> None:
        declared = self.headers.get("content-length")
        if declared is None:
            return
        if not (declared.isascii() and declared.isdigit()):
            raise HTTPError(400, "Bad Request", "invalid Content-Length header")
        if self._max_body_size is not None and int(declared) > self._max_body_size:
            raise self._too_large()

    async def _chunks(self) -> AsyncIterator[bytes]:
        received_bytes = 0
        async for chunk in self._source:
            if not chunk:
                continue
            received_bytes += len(chunk)
            if self._max_body_size is not None and received_bytes > self._max_body_size:
                raise self._too_large()
            yield chunk

    def _too_large(self) -> HTTPError:
        return HTTPError(
            413, "Payload Too Large", f"body exceeds {self._max_body_size} bytes"
        )
