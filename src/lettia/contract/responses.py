"""Response classes: one class per status code, all msgspec structs."""

from collections.abc import AsyncIterator, Callable
from typing import ClassVar

import msgspec


class Reply(msgspec.Struct):
    """Base of every response class a contract handler may return."""

    status: ClassVar[int]
    media_type: ClassVar[str] = "application/json"


class StreamReply[T](Reply):
    """Extension hook for a streamed response whose body is ``AsyncIterator[T]``.

    Not returnable by itself: a subclass declares ``status`` and ``media_type``
    and overrides :meth:`frames`, which turns the items into the wire bytes.
    ``mount`` reports a subclass that does not override ``frames``.
    ``default_headers`` are added to every response (never ``Content-Type`` or
    ``Content-Length``, never a name a ``HeaderName`` field also uses), and
    ``watch_disconnect`` asks the server adapter to stop the stream when the
    client goes away. With ``debug`` on, ``check`` validates each item and raises
    ``RuntimeError`` on a mismatch.
    """

    body: AsyncIterator[T]
    default_headers: ClassVar[tuple[tuple[str, str], ...]] = ()
    watch_disconnect: ClassVar[bool] = False

    def frames(
        self,
        encode: Callable[[object], bytes],
        *,
        check: Callable[[object], None] | None = None,
    ) -> AsyncIterator[bytes]:
        raise TypeError("StreamReply requires a frames implementation")


class Ok[T](Reply):
    status = 200
    body: T


class Created[T](Reply):
    status = 201
    body: T


class NoContent(Reply):
    status = 204


class NotFound[T](Reply):
    status = 404
    body: T


class Problem(msgspec.Struct):
    """RFC 9457 problem details."""

    title: str
    detail: str | None = None
