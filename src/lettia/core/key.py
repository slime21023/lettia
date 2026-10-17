"""Typed request-scoped storage."""

from typing import Any, cast, overload

from lettia.core.context import Context

_UNSET = object()


class Key[T]:
    """A typed slot in the request context.

    The accessors live on the key so that ``T`` is fixed by the instance. Keys
    are compared by identity: two keys with the same name never collide.
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def set(self, c: Context, value: T) -> None:
        c.state[self] = value

    @overload
    def get(self, c: Context, /) -> T: ...
    @overload
    def get[D](self, c: Context, default: D, /) -> T | D: ...
    def get(self, c: Context, default: Any = _UNSET, /) -> Any:
        """Return the stored value or ``default``; raise ``LookupError`` when unset."""
        value = c.state.get(self, _UNSET)
        if value is not _UNSET:
            return cast(T, value)
        if default is not _UNSET:
            return default
        raise LookupError(f"request value {self.name!r} is not set")
