# pyright: reportUnknownMemberType=false
"""Middleware managing Rodi ActivationScope per request and async teardown."""

from typing import cast

import rodi

from lettia.core import Context, Handler, Key, Response
from lettia.extensions.di.descriptor import current_scope
from lettia.extensions.di.teardown import teardown_scope

SCOPE_KEY = Key[rodi.ActivationScope]("lettia.extensions.di.scope")
"""Key for accessing the current Rodi ActivationScope on Context.state."""


class DIMiddleware:
    """Manages an ActivationScope per request, injecting it into Context."""

    def __init__(self, container: rodi.Container | rodi.Services) -> None:
        if isinstance(container, rodi.Container):
            self._provider = container.build_provider()
        else:
            self._provider = container

    @property
    def provider(self) -> rodi.Services:
        """The underlying rodi.Services instance."""
        return self._provider

    def __call__(self, next_handler: Handler) -> Handler:
        async def run(c: Context) -> Response:
            scope = self._provider.create_scope()
            token = current_scope.set(scope)
            SCOPE_KEY.set(c, scope)
            try:
                return await next_handler(c)
            finally:
                try:
                    await teardown_scope(scope)
                finally:
                    current_scope.reset(token)
                    scope.dispose()  # type: ignore[no-untyped-call]

        return cast(Handler, run)
