"""Response classes: one class per status code, all msgspec structs."""

from collections.abc import AsyncIterator, Callable
from typing import Annotated, ClassVar

import msgspec

from lettia.contract.markers import HeaderName


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


class Accepted[T](Reply):
    status = 202
    body: T


class NoContent(Reply):
    status = 204


class BadRequest[T](Reply):
    status = 400
    body: T


class NotFound[T](Reply):
    status = 404
    body: T


class Conflict[T](Reply):
    status = 409
    body: T


class _Redirect(Reply):
    """A redirect: no body, and the ``Location`` header is required."""

    location: Annotated[str, HeaderName("Location")]


class MovedPermanently(_Redirect):
    status = 301


class Found(_Redirect):
    status = 302


class SeeOther(_Redirect):
    status = 303


class TemporaryRedirect(_Redirect):
    status = 307


class PermanentRedirect(_Redirect):
    status = 308


class Problem(msgspec.Struct):
    """RFC 9457 problem details."""

    title: str
    detail: str | None = None
