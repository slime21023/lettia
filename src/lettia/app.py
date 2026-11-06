"""The public ``App`` and ``Group``: Core plus ``mount`` for contracts."""

import json
from collections.abc import Sequence
from typing import Any, cast, override

from lettia.contract.codec import Codec
from lettia.contract.context import Context
from lettia.contract.guards import GuardImpl
from lettia.contract.mount import OperationMiddleware, contract_registry, mount
from lettia.contract.openapi import OpenAPIInfo, build_openapi
from lettia.core import app as core_app
from lettia.core.context import Context as CoreContext
from lettia.core.handler import Middleware
from lettia.core.headers import MutableHeaders
from lettia.core.problem import ErrorHandler
from lettia.core.request import DEFAULT_MAX_BODY_SIZE
from lettia.core.response import Response
from lettia.extensions.docs import Provider, mount_docs

_JSON_TYPE = (b"content-type", b"application/json")

type DocsUI = Provider


class Group(core_app.Group):
    """A route group that can also mount contract implementations."""

    def mount(
        self,
        implementation: object,
        *,
        codec: Codec | None = None,
        middleware: OperationMiddleware | None = None,
    ) -> None:
        """Register the operations of the contracts ``implementation`` inherits.

        ``middleware`` wraps single operations, keyed by contract method.
        """
        mount(self, implementation, codec=codec, middleware=middleware)

    @override
    def group(self, prefix: str, *middleware: Middleware) -> "Group":
        return cast(Group, super().group(prefix, *middleware))


class App(core_app.App):
    """An ASGI application whose handlers receive ``Context[G]``.

    The OpenAPI document is derived from the mounted contracts when the app is
    built and served at ``openapi_url`` (``None`` turns the endpoint off). With
    ``docs_url`` an interactive viewer (``docs_ui``: Scalar, Swagger UI or
    Redoc, loaded from a CDN) is served too; ``mount_docs`` offers more
    options (theme, favicon, local assets).
    """

    _group_class = Group

    def __init__(
        self,
        *,
        debug: bool = False,
        error_handler: ErrorHandler | None = None,
        context_class: type[Context[Any]] = Context,
        max_body_size: int | None = DEFAULT_MAX_BODY_SIZE,
        allowed_hosts: Sequence[str] | None = None,
        guards: Sequence[GuardImpl[Any]] = (),
        codec: Codec | None = None,
        title: str = "API",
        version: str = "0.0.0",
        description: str | None = None,
        openapi_url: str | None = "/openapi.json",
        docs_url: str | None = None,
        docs_ui: DocsUI = "scalar",
    ) -> None:
        super().__init__(
            debug=debug,
            error_handler=error_handler,
            context_class=context_class,
            max_body_size=max_body_size,
            allowed_hosts=allowed_hosts,
        )
        self._contracts = contract_registry(self)
        if codec is not None:
            self._contracts.codec = codec
        self._contracts.register_guards(self, guards)
        self._openapi_info = OpenAPIInfo(title, version, description)
        self._openapi_url = openapi_url
        self._openapi_json: bytes | None = None
        self.add_startup_check(self._derive_openapi)
        if openapi_url is not None:
            self.get(openapi_url, self._serve_openapi)
        if docs_url is not None:
            mount_docs(self, docs_url, docs_ui)

    def mount(
        self,
        implementation: object,
        *,
        codec: Codec | None = None,
        middleware: OperationMiddleware | None = None,
    ) -> None:
        """Register the operations of the contracts ``implementation`` inherits.

        ``middleware`` wraps single operations, keyed by contract method, for
        example ``{Api.login: [rate_limit("5/minute")]}``; it runs outside the
        operation's guard, inside app and group middleware.
        """
        mount(self, implementation, codec=codec, middleware=middleware)

    @override
    def group(self, prefix: str, *middleware: Middleware) -> Group:
        return cast(Group, super().group(prefix, *middleware))

    # --- OpenAPI ----------------------------------------------------------

    def openapi(self) -> dict[str, Any]:
        """The OpenAPI 3.1 document of the mounted contracts (builds the app)."""
        self.build()
        if self._openapi_json is None:
            raise RuntimeError("the OpenAPI document was not derived")
        return cast(dict[str, Any], json.loads(self._openapi_json))

    def _derive_openapi(self) -> list[str]:
        codec = self._contracts.codec
        document, problems = build_openapi(
            self._contracts.mounted, info=self._openapi_info, codec=codec
        )
        if document is not None:
            self._openapi_json = codec.build_encoder()(document)
        return problems

    async def _serve_openapi(self, c: CoreContext) -> Response:
        return Response(200, self._openapi_json, MutableHeaders([_JSON_TYPE]))

    @property
    def title(self) -> str:
        """The title of the OpenAPI document."""
        return self._openapi_info.title

    @property
    def openapi_url(self) -> str | None:
        """Where the OpenAPI document is served; ``None`` if it is off."""
        return self._openapi_url
