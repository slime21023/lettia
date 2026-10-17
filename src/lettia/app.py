"""The public ``App`` and ``Group``: Core plus ``mount`` for contracts."""

from typing import Any, cast, override

from lettia.contract.codec import Codec
from lettia.contract.context import Context
from lettia.contract.mount import mount
from lettia.core import app as core_app
from lettia.core.handler import Middleware
from lettia.core.problem import ErrorHandler
from lettia.core.request import DEFAULT_MAX_BODY_SIZE


class Group(core_app.Group):
    """A route group that can also mount contract implementations."""

    def mount(self, implementation: object, *, codec: Codec | None = None) -> None:
        """Register the operations of the contracts ``implementation`` inherits."""
        mount(self, implementation, codec=codec)

    @override
    def group(self, prefix: str, *middleware: Middleware) -> "Group":
        return cast(Group, super().group(prefix, *middleware))


class App(core_app.App):
    """An ASGI application whose handlers receive ``Context[G]``."""

    _group_class = Group

    def __init__(
        self,
        *,
        debug: bool = False,
        error_handler: ErrorHandler | None = None,
        context_class: type[Context[Any]] = Context,
        max_body_size: int | None = DEFAULT_MAX_BODY_SIZE,
    ) -> None:
        super().__init__(
            debug=debug,
            error_handler=error_handler,
            context_class=context_class,
            max_body_size=max_body_size,
        )

    def mount(self, implementation: object, *, codec: Codec | None = None) -> None:
        """Register the operations of the contracts ``implementation`` inherits."""
        mount(self, implementation, codec=codec)

    @override
    def group(self, prefix: str, *middleware: Middleware) -> Group:
        return cast(Group, super().group(prefix, *middleware))
