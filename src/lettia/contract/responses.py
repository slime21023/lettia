"""Response classes: one class per status code, all msgspec structs."""

from typing import ClassVar

import msgspec


class Reply(msgspec.Struct):
    """Base of every response class a contract handler may return."""

    status: ClassVar[int]
    media_type: ClassVar[str] = "application/json"


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
