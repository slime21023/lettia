"""Request-field source markers.

Every field of a request ``Struct`` is marked with exactly one source. The
markers are type aliases, so checkers see the field as plain ``T``; the
framework recognizes the source with ``get_origin(hint) is Path``.
"""

from typing import Annotated, final


@final
class _Source:
    def __init__(self, name: str) -> None:
        self.name = name


@final
class HeaderName:
    """Custom wire name: ``Annotated[str, HeaderName("X-Custom")]``."""

    def __init__(self, name: str) -> None:
        self.name = name


type Path[T] = Annotated[T, _Source("path")]
type Query[T] = Annotated[T, _Source("query")]
type Header[T] = Annotated[T, _Source("header")]
type Cookie[T] = Annotated[T, _Source("cookie")]
type Body[T] = Annotated[T, _Source("body")]
