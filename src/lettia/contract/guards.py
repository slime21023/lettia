"""Guards: declared on the contract, implemented at assembly time.

A ``GuardSpec`` is a small tree. A named guard is a leaf; ``a | b`` and ``a & b``
build composite nodes whose static type is inferred from their parts. What each
node means at runtime lives in :mod:`lettia.contract.guard_runtime`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, cast

from lettia.core.request import RequestHead


@dataclass(frozen=True, slots=True)
class HttpBearer:
    format: str | None = None


@dataclass(frozen=True, slots=True)
class ApiKeyHeader:
    name: str


type SecurityScheme = HttpBearer | ApiKeyHeader
type GuardKind = Literal["leaf", "or", "and"]


class GuardImpl[G]:
    """A guard implementation bound to its spec by :meth:`GuardSpec.use`."""

    __slots__ = ("fn", "spec")

    def __init__(
        self, spec: GuardSpec[G], fn: Callable[[RequestHead], Awaitable[G]]
    ) -> None:
        self.spec = spec
        self.fn = fn


class GuardSpec[G]:
    """Declares the principal type ``G`` and the OpenAPI ``security``.

    Specs are compared by identity: the implementation registered for a spec is
    the one passed to ``spec.use``, not one for any spec with the same name.
    """

    __slots__ = ("_kind", "_parts", "name", "scheme")

    def __init__(self, name: str, *, scheme: SecurityScheme | None = None) -> None:
        self.name = name
        self.scheme = scheme
        self._kind: GuardKind = "leaf"
        self._parts: tuple[GuardSpec[Any], ...] = ()

    @classmethod
    def _composite(
        cls, kind: GuardKind, parts: tuple[GuardSpec[Any], ...]
    ) -> GuardSpec[Any]:
        glue = " | " if kind == "or" else " & "
        spec = cls(f"({glue.join(part.name for part in parts)})")
        spec._kind = kind
        spec._parts = parts
        return spec

    @property
    def kind(self) -> GuardKind:
        return self._kind

    @property
    def parts(self) -> tuple[GuardSpec[Any], ...]:
        """The operands of a composite; empty for a named guard."""
        return self._parts

    def leaves(self) -> tuple[GuardSpec[Any], ...]:
        """Every named guard this spec is made of, once each, in order."""
        if self._kind == "leaf":
            return (self,)
        found = (leaf for part in self._parts for leaf in part.leaves())
        return tuple(dict.fromkeys(found))

    def __or__[H](self, other: GuardSpec[H]) -> GuardSpec[G | H]:
        """Try in order; the first success wins."""
        parts = (*self._operands("or"), *other._operands("or"))
        return cast(GuardSpec[G | H], GuardSpec._composite("or", parts))

    def __and__[H](self, other: GuardSpec[H]) -> GuardSpec[tuple[G, H]]:
        """Run in order; any failure aborts."""
        return cast(GuardSpec[tuple[G, H]], GuardSpec._composite("and", (self, other)))

    def _operands(self, kind: GuardKind) -> tuple[GuardSpec[Any], ...]:
        return self._parts if self._kind == kind else (self,)

    def use(self, fn: Callable[[RequestHead], Awaitable[G]]) -> GuardImpl[G]:
        """Bind the implementation of this guard; ``fn`` returns the principal."""
        if self._kind != "leaf":
            raise TypeError(
                f"the composite guard {self.name} cannot be implemented; "
                "implement each guard it is made of"
            )
        if self is public:
            raise TypeError("the public guard needs no implementation")
        return GuardImpl(self, fn)


public = GuardSpec[None]("public")
