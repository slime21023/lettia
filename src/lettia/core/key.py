"""Typed request-scoped storage."""

from typing import cast

from lettia.core.context import Context


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

    def get(self, c: Context) -> T:
        """Return the stored value; raise ``LookupError`` when unset."""
        try:
            return cast(T, c.state[self])
        except KeyError:
            raise LookupError(f"request value {self.name!r} is not set") from None
