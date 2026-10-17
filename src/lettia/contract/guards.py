"""Guards: declared on the contract, implemented at assembly time."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


class RequestHead:
    """The request head a guard may read; guards run before decoding."""


@dataclass(frozen=True, slots=True)
class HttpBearer:
    format: str | None = None


@dataclass(frozen=True, slots=True)
class ApiKeyHeader:
    name: str


type SecurityScheme = HttpBearer | ApiKeyHeader


class GuardImpl[G]:
    """A guard implementation bound to its spec by :meth:`GuardSpec.use`."""


class GuardSpec[G]:
    """Declares the principal type ``G`` and the OpenAPI ``security``."""

    def __init__(self, name: str, *, scheme: SecurityScheme | None = None) -> None:
        self.name = name
        self.scheme = scheme

    def __or__[H](self, other: GuardSpec[H]) -> GuardSpec[G | H]:
        """Try in order; the first success wins."""
        raise NotImplementedError

    def __and__[H](self, other: GuardSpec[H]) -> GuardSpec[tuple[G, H]]:
        """Run in order; any failure aborts."""
        raise NotImplementedError

    def use(self, fn: Callable[[RequestHead], Awaitable[G]]) -> GuardImpl[G]:
        raise NotImplementedError


public = GuardSpec[None]("public")
