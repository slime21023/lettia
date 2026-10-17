"""Typed request-scoped storage."""

from lettia.core.context import Context


class Key[T]:
    """A typed slot in the request context.

    The accessors live on the key so that ``T`` is fixed by the instance.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def set(self, c: Context, value: T) -> None:
        raise NotImplementedError

    def get(self, c: Context) -> T:
        """Return the stored value; raise ``LookupError`` when unset."""
        raise NotImplementedError
