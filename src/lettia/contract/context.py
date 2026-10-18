"""Context carrying the principal determined by an operation's guard."""

from typing import cast

from lettia.core.context import Context as CoreContext
from lettia.core.request import Request


class Context[G](CoreContext):
    """Request context whose ``principal`` type is fixed by the guard ``G``.

    For a ``public`` operation ``G`` is ``None`` and so is the principal.
    """

    __slots__ = ("principal",)

    def __init__(
        self, request: Request, path_params: dict[str, str] | None = None
    ) -> None:
        super().__init__(request, path_params)
        self.principal: G = cast(G, None)
