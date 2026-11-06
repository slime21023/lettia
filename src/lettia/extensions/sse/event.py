"""The Server-Sent Events item type and its wire framing."""

import re
from collections.abc import Callable
from typing import Any, cast

import msgspec

_LINE_BREAK = re.compile(r"\r\n|\r|\n")


class ServerSentEvent[T](msgspec.Struct):
    """One event. ``data`` is sent as JSON, so a client reads it with JSON.parse.

    ``event``, ``id`` and ``retry`` map to the SSE fields of the same name.
    """

    data: T
    event: str | None = None
    id: str | None = None
    retry: int | None = None

    def __post_init__(self) -> None:
        _validate(self)


def _validate(item: ServerSentEvent[Any]) -> None:
    # A Struct can be mutated after construction, so this runs again on send.
    for name in ("event", "id"):
        value: object = getattr(item, name)
        if value is None:
            continue
        if not isinstance(value, str):
            raise TypeError(
                f"ServerSentEvent.{name} must be str or None, "
                f"got {type(value).__name__}"
            )
        if "\r" in value or "\n" in value:
            raise ValueError(f"ServerSentEvent.{name} must not contain a line break")
        if name == "id" and "\0" in value:
            raise ValueError("ServerSentEvent.id must not contain NUL")
    retry = cast(object, item.retry)
    if retry is None:
        return
    if not isinstance(retry, int) or isinstance(retry, bool):
        raise TypeError(
            f"ServerSentEvent.retry must be int or None, got {type(retry).__name__}"
        )
    if retry < 0:
        raise ValueError("ServerSentEvent.retry must not be negative")


def _as_event(item: object) -> ServerSentEvent[Any]:
    if not isinstance(item, ServerSentEvent):
        raise TypeError(
            f"a Server-Sent Events stream yields ServerSentEvent, "
            f"got {type(item).__name__}"
        )
    return item  # pyright: ignore[reportUnknownVariableType]


def encode_event(item: object, encode: Callable[[object], bytes]) -> bytes:
    """The wire bytes of one event; ``item`` must be a ``ServerSentEvent``."""
    event = _as_event(item)
    _validate(event)
    lines: list[str] = []
    if event.event is not None:
        lines.append(f"event: {event.event}\n")
    if event.id is not None:
        lines.append(f"id: {event.id}\n")
    if event.retry is not None:
        lines.append(f"retry: {event.retry}\n")
    text = encode(event.data).decode("utf-8")
    lines.extend(f"data: {line}\n" for line in _LINE_BREAK.split(text))
    lines.append("\n")
    return "".join(lines).encode("utf-8")
