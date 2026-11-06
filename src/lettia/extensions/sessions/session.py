"""The request session: JSON-like data plus one-shot flash messages."""

import math
from collections.abc import Iterator, MutableMapping
from typing import cast

from lettia.core import Context as CoreContext
from lettia.core import Key

type SessionValue = (
    str | int | float | bool | None | list[SessionValue] | dict[str, SessionValue]
)

MAX_DEPTH = 32
_INT_MIN = -(2**63)
_INT_MAX = 2**64 - 1


def _enter(container: object, depth: int, active: set[int]) -> None:
    if depth > MAX_DEPTH:
        raise ValueError(f"session values nest deeper than {MAX_DEPTH}")
    marker = id(container)
    if marker in active:
        raise ValueError("session values must not be circular")
    active.add(marker)


def _check(value: object, depth: int, active: set[int]) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        if not _INT_MIN <= value <= _INT_MAX:
            raise ValueError("session integer is out of range")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("session floats must be finite")
        return
    if isinstance(value, list):
        items = cast(list[object], value)
        _enter(items, depth, active)
        try:
            for item in items:
                _check(item, depth + 1, active)
        finally:
            active.discard(id(items))
        return
    if isinstance(value, dict):
        entries = cast(dict[object, object], value)
        _enter(entries, depth, active)
        try:
            for name, item in entries.items():
                if not isinstance(name, str):
                    raise ValueError("session dict keys must be strings")
                _check(item, depth + 1, active)
        finally:
            active.discard(id(entries))
        return
    raise ValueError(f"session value of type {type(value).__name__} is not JSON-like")


def validate_data(data: object) -> None:
    """Raise ``ValueError`` unless ``data`` is a dict of JSON-like session values."""
    if not isinstance(data, dict):
        raise ValueError("session data must be a dict")
    _check(cast(dict[object, object], data), 0, set())


def validate_flash(flash: object) -> None:
    """Raise ``ValueError`` unless ``flash`` is a list of ``(str, str)`` pairs."""
    if not isinstance(flash, list):
        raise ValueError("session flash must be a list")
    for item in cast(list[object], flash):
        if not isinstance(item, tuple):
            raise ValueError("session flash items must be (str, str) pairs")
        pair = cast(tuple[object, ...], item)
        if not (
            len(pair) == 2 and isinstance(pair[0], str) and isinstance(pair[1], str)
        ):
            raise ValueError("session flash items must be (str, str) pairs")


class Session(MutableMapping[str, SessionValue]):
    """Mutable mapping of session data, plus flash messages kept apart from it.

    Values must be JSON-like: ``str``, ``int``, ``float``, ``bool``, ``None``,
    lists and string-keyed dicts nested at most 32 levels deep. Anything else
    makes the response fail with ``ValueError`` when the session is committed.

    Flash messages read with :meth:`pop_flashes` are consumed; unread ones stay
    until they are consumed or the cookie expires. Flashes added during this
    request are only delivered on a later one. Exactly-once delivery is not
    promised for parallel requests.
    """

    __slots__ = ("_data", "_loaded_flash", "_new_flash")

    def __init__(
        self,
        data: dict[str, SessionValue] | None = None,
        flash: list[tuple[str, str]] | None = None,
    ) -> None:
        self._data: dict[str, SessionValue] = {} if data is None else data
        self._loaded_flash: list[tuple[str, str]] = [] if flash is None else flash
        self._new_flash: list[tuple[str, str]] = []

    @classmethod
    def of(cls, c: CoreContext) -> "Session":
        """The session of this request; ``RuntimeError`` without the middleware."""
        session = SESSION_KEY.get(c, None)
        if session is None:
            raise RuntimeError("SessionMiddleware is not installed")
        return session

    def __getitem__(self, key: str) -> SessionValue:
        return self._data[key]

    def __setitem__(self, key: str, value: SessionValue) -> None:
        self._data[key] = value

    def __delitem__(self, key: str) -> None:
        del self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def flash(self, message: str, category: str = "message") -> None:
        """Queue a message for the next request that reads flashes."""
        if type(message) is not str or type(category) is not str:
            raise TypeError("flash message and category must be str")
        self._new_flash.append((category, message))

    def pop_flashes(self) -> list[tuple[str, str]]:
        """Return and clear the flashes carried in from earlier requests."""
        flashes = self._loaded_flash
        self._loaded_flash = []
        return flashes

    def export(self) -> tuple[dict[str, SessionValue], list[tuple[str, str]]]:
        """The live data and the flashes to persist (unread, then new).

        For the middleware that commits the session.
        """
        return self._data, [*self._loaded_flash, *self._new_flash]


SESSION_KEY = Key[Session]("lettia.extensions.sessions")
