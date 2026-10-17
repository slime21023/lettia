"""Context carrying the principal determined by an operation's guard."""

from lettia.core.context import Context as CoreContext


class Context[G](CoreContext):
    """Request context whose ``principal`` type is fixed by the guard ``G``."""

    @property
    def principal(self) -> G:
        raise NotImplementedError
