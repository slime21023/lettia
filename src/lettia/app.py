"""The public ``App`` and ``Group``: Core plus ``mount`` for contracts."""

import json
from collections.abc import Sequence
from typing import Any, cast, override

from lettia.contract.codec import Codec, MsgspecCodec
from lettia.contract.context import Context
from lettia.contract.guards import GuardImpl, GuardSpec
from lettia.contract.mount import GUARDS_KEY, MOUNTED_KEY, MountedOperation, mount
from lettia.contract.openapi import OpenAPIInfo, build_openapi
from lettia.core import app as core_app
from lettia.core.context import Context as CoreContext
from lettia.core.handler import Middleware
from lettia.core.headers import MutableHeaders
from lettia.core.problem import ErrorHandler
from lettia.core.request import DEFAULT_MAX_BODY_SIZE
from lettia.core.response import Response

_JSON_TYPE = (b"content-type", b"application/json")


class Group(core_app.Group):
    """A route group that can also mount contract implementations."""

    def mount(self, implementation: object, *, codec: Codec | None = None) -> None:
        """Register the operations of the contracts ``implementation`` inherits."""
        mount(self, implementation, codec=codec)

    @override
    def group(self, prefix: str, *middleware: Middleware) -> "Group":
        return cast(Group, super().group(prefix, *middleware))


class App(core_app.App):
    """An ASGI application whose handlers receive ``Context[G]``.

    The OpenAPI document is derived from the mounted contracts when the app is
    built and served at ``openapi_url`` (``None`` turns the endpoint off).
    """

    _group_class = Group

    def __init__(
        self,
        *,
        debug: bool = False,
        error_handler: ErrorHandler | None = None,
        context_class: type[Context[Any]] = Context,
        max_body_size: int | None = DEFAULT_MAX_BODY_SIZE,
        guards: Sequence[GuardImpl[Any]] = (),
        title: str = "API",
        version: str = "0.0.0",
        description: str | None = None,
        openapi_url: str | None = "/openapi.json",
    ) -> None:
        super().__init__(
            debug=debug,
            error_handler=error_handler,
            context_class=context_class,
            max_body_size=max_body_size,
        )
        self.state[GUARDS_KEY] = self._register_guards(guards)
        self._openapi_info = OpenAPIInfo(title, version, description)
        self._openapi_codec = MsgspecCodec()
        self._openapi_json: bytes | None = None
        self.add_startup_check(self._derive_openapi)
        if openapi_url is not None:
            self.get(openapi_url, self._serve_openapi)

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

    # --- OpenAPI ----------------------------------------------------------

    def openapi(self) -> dict[str, Any]:
        """The OpenAPI 3.1 document of the mounted contracts (builds the app)."""
        self.build()
        if self._openapi_json is None:
            raise RuntimeError("the OpenAPI document was not derived")
        return cast(dict[str, Any], json.loads(self._openapi_json))

    def _derive_openapi(self) -> list[str]:
        mounted = cast(list[MountedOperation], self.state.get(MOUNTED_KEY, []))
        document, problems = build_openapi(
            mounted, info=self._openapi_info, codec=self._openapi_codec
        )
        if document is not None:
            self._openapi_json = self._openapi_codec.build_encoder()(document)
        return problems

    async def _serve_openapi(self, c: CoreContext) -> Response:
        return Response(200, self._openapi_json, MutableHeaders([_JSON_TYPE]))
