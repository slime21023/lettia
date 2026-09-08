"""Typed, context-local state for middleware and handlers."""

from typing import TypeVar, cast

from attrs import define, field

from lettia.asgi import JSONValue

T = TypeVar("T")
_MISSING = object()


@define(frozen=True, slots=True, eq=False)
class StateKey[T]:
    """An identity-based, typed key for a value in a :class:`StateStore`."""

    name: str


@define(slots=True)
class StateStore:
    """A typed-key store whose values live for one context only."""

    _values: dict[StateKey[object], object] = field(
        factory=dict[StateKey[object], object]
    )

    def set(self, key: StateKey[T], value: T) -> None:
        self._values[cast(StateKey[object], key)] = value

    def get(self, key: StateKey[T]) -> T | None:
        value = self._values.get(cast(StateKey[object], key))
        return cast(T | None, value)

    def require(self, key: StateKey[T]) -> T:
        value = self._values.get(cast(StateKey[object], key), _MISSING)
        if value is _MISSING:
            raise KeyError(f"State key is not set: {key.name}")
        return cast(T, value)

    def discard(self, key: StateKey[T]) -> None:
        self._values.pop(cast(StateKey[object], key), None)


type SessionData = dict[str, JSONValue]

REQUEST_ID: StateKey[str] = StateKey("lettia.request_id")
SESSION: StateKey[SessionData] = StateKey("lettia.session")
