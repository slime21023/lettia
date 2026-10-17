"""The public ``App`` and ``Group``: Core plus ``mount`` for contracts."""

from collections.abc import Sequence
from typing import Any, cast, override

from lettia.contract.codec import Codec
from lettia.contract.context import Context
from lettia.contract.guards import GuardImpl, GuardSpec
from lettia.contract.mount import GUARDS_KEY, mount
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
        guards: Sequence[GuardImpl[Any]] = (),
    ) -> None:
        super().__init__(
            debug=debug,
            error_handler=error_handler,
            context_class=context_class,
            max_body_size=max_body_size,
        )
        self.state[GUARDS_KEY] = self._register_guards(guards)

    def _register_guards(
        self, guards: Sequence[GuardImpl[Any]]
    ) -> dict[GuardSpec[Any], GuardImpl[Any]]:
        registry: dict[GuardSpec[Any], GuardImpl[Any]] = {}
        for implementation in guards:
            if not isinstance(cast(object, implementation), GuardImpl):
                raise TypeError(
                    "guards must be made with spec.use(fn), "
                    f"got {type(implementation).__name__}"
                )
            if implementation.spec in registry:
                self.add_startup_problem(
                    f"guard {implementation.spec.name!r} is implemented more than once"
                )
                continue
            registry[implementation.spec] = implementation
        return registry

    def mount(self, implementation: object, *, codec: Codec | None = None) -> None:
        """Register the operations of the contracts ``implementation`` inherits."""
        mount(self, implementation, codec=codec)

    @override
    def group(self, prefix: str, *middleware: Middleware) -> Group:
        return cast(Group, super().group(prefix, *middleware))
