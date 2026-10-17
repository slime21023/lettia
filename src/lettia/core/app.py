"""``App`` and ``Group``: route registration, middleware, errors and lifespan."""

import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from lettia.core.asgi import ASGIApp, Message, Receive, Scope, Send, asgi_app
from lettia.core.context import Context
from lettia.core.errors import ClientDisconnect, HTTPError, StartupError
from lettia.core.handler import Handler, Middleware
from lettia.core.problem import ErrorHandler, default_error_handler, problem_response
from lettia.core.request import DEFAULT_MAX_BODY_SIZE
from lettia.core.response import Response
from lettia.core.router import Matched, MethodNotAllowed, Router

type Hook = Callable[[], Awaitable[None]]

logger = logging.getLogger("lettia")


class _Routes(ABC):
    """The route-registration surface shared by ``App`` and ``Group``."""

    @abstractmethod
    def route(self, method: str, path: str, handler: Handler) -> None: ...

    @abstractmethod
    def group(self, prefix: str, *middleware: Middleware) -> "Group": ...

    def get(self, path: str, handler: Handler) -> None:
        self.route("GET", path, handler)

    def post(self, path: str, handler: Handler) -> None:
        self.route("POST", path, handler)

    def put(self, path: str, handler: Handler) -> None:
        self.route("PUT", path, handler)

    def patch(self, path: str, handler: Handler) -> None:
        self.route("PATCH", path, handler)

    def delete(self, path: str, handler: Handler) -> None:
        self.route("DELETE", path, handler)

    def options(self, path: str, handler: Handler) -> None:
        self.route("OPTIONS", path, handler)


@dataclass(frozen=True, slots=True)
class _RouteSpec:
    method: str
    path: str
    handler: Handler
    middleware: tuple[Middleware, ...]


def _check_prefix(prefix: str) -> str:
    if not prefix.startswith("/") or prefix.endswith("/"):
        raise ValueError(
            f"invalid group prefix {prefix!r}: "
            "it must start with a slash and must not end with one"
        )
    return prefix


def _compose(middleware: Sequence[Middleware], handler: Handler) -> Handler:
    for wrap in reversed(middleware):
        handler = wrap(handler)
    return handler


class Group(_Routes):
    """Routes under a common prefix, with middleware that applies only to them."""

    def __init__(
        self,
        register: Callable[[_RouteSpec], None],
        ensure_open: Callable[[], None],
        prefix: str,
        middleware: tuple[Middleware, ...],
    ) -> None:
        self._register = register
        self._ensure_open = ensure_open
        self._prefix = prefix
        self._middleware = middleware

    def route(self, method: str, path: str, handler: Handler) -> None:
        self._register(
            _RouteSpec(method, self._prefix + path, handler, self._middleware)
        )

    def group(self, prefix: str, *middleware: Middleware) -> "Group":
        self._ensure_open()
        return Group(
            self._register,
            self._ensure_open,
            self._prefix + _check_prefix(prefix),
            (*self._middleware, *middleware),
        )


class App(_Routes):
    """An ASGI application.

    Routes, middleware and hooks are collected first and assembled once, by
    :meth:`build`, when the lifespan starts (or on the first request without
    one). Request handling then only looks up a route and calls its prebuilt
    chain: app middleware, group middleware, error conversion, handler.
    """

    def __init__(
        self,
        *,
        debug: bool = False,
        error_handler: ErrorHandler | None = None,
        context_class: type[Context] = Context,
        max_body_size: int | None = DEFAULT_MAX_BODY_SIZE,
    ) -> None:
        self._error_handler = error_handler or default_error_handler(debug=debug)
        self._context_class = context_class
        self._max_body_size = max_body_size
        self._routes: list[_RouteSpec] = []
        self._middleware: list[Middleware] = []
        self._startup: list[Hook] = []
        self._shutdown: list[Hook] = []
        self._http: ASGIApp | None = None
        self._not_allowed: dict[str, Handler] = {}

    # --- registration ---------------------------------------------------

    def route(self, method: str, path: str, handler: Handler) -> None:
        self._register(_RouteSpec(method, path, handler, ()))

    def group(self, prefix: str, *middleware: Middleware) -> Group:
        self._ensure_open()
        return Group(
            self._register, self._ensure_open, _check_prefix(prefix), middleware
        )

    def use(self, *middleware: Middleware) -> None:
        """Add global middleware; the first one given is the outermost."""
        self._ensure_open()
        self._middleware.extend(middleware)

    def on_startup(self, hook: Hook) -> None:
        self._ensure_open()
        self._startup.append(hook)

    def on_shutdown(self, hook: Hook) -> None:
        self._ensure_open()
        self._shutdown.append(hook)

    def _register(self, spec: _RouteSpec) -> None:
        self._ensure_open()
        self._routes.append(spec)

    def _ensure_open(self) -> None:
        if self._http is not None:
            raise RuntimeError("the app is already built; register before it starts")

    # --- assembly -------------------------------------------------------

    def build(self) -> None:
        """Run the startup checks and assemble every chain; idempotent."""
        self._built_http()

    def _built_http(self) -> ASGIApp:
        if self._http is None:
            self._http = self._assemble()
        return self._http

    def _assemble(self) -> ASGIApp:
        problems: list[str] = []
        router = Router()
        for spec in self._routes:
            chain = self._chain(spec.middleware, spec.handler)
            try:
                router.add(spec.method, spec.path, chain)
            except ValueError as exc:
                problems.append(str(exc))
        if problems:
            raise StartupError(problems)
        not_found = self._chain((), _raise_not_found)

        async def dispatch(c: Context) -> Response:
            request = c.request
            result = router.match(request.method, request.raw_path)
            try:
                if isinstance(result, Matched):
                    c.path_params = result.path_params
                    return await result.handler(c)
                if isinstance(result, MethodNotAllowed):
                    return await self._method_not_allowed(result.allow_header)(c)
                return await not_found(c)
            except ClientDisconnect:
                raise
            except Exception as exc:  # raised by middleware, outside the inner guard
                return await self._to_response(c, exc)

        return asgi_app(
            dispatch,
            max_body_size=self._max_body_size,
            context_class=self._context_class,
        )

    def _chain(
        self, group_middleware: Sequence[Middleware], handler: Handler
    ) -> Handler:
        guarded = self._convert_errors(handler)
        return _compose([*self._middleware, *group_middleware], guarded)

    def _convert_errors(self, handler: Handler) -> Handler:
        """Turn exceptions into responses so middleware only ever sees responses."""

        async def run(c: Context) -> Response:
            try:
                return await handler(c)
            except ClientDisconnect:
                raise
            except Exception as exc:
                return await self._to_response(c, exc)

        return run

    async def _to_response(self, c: Context, exc: Exception) -> Response:
        if not isinstance(exc, HTTPError):
            logger.error(
                "unhandled exception on %s %s",
                c.request.method,
                c.request.path,
                exc_info=exc,
            )
        try:
            return await self._error_handler(c, exc)
        except Exception:
            logger.exception("the error handler failed")
            return problem_response(500, "Internal Server Error")

    def _method_not_allowed(self, allow: str) -> Handler:
        chain = self._not_allowed.get(allow)
        if chain is None:
            chain = self._chain((), _raise_method_not_allowed(allow))
            self._not_allowed[allow] = chain
        return chain

    # --- ASGI -----------------------------------------------------------

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "http":
            http = self._http or self._built_http()
            try:
                await http(scope, receive, send)
            except ClientDisconnect:
                return  # nobody is left to answer
        elif kind == "lifespan":
            await self._lifespan(receive, send)
        else:
            raise NotImplementedError(f"unsupported ASGI scope: {kind}")

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                failure = await self._start()
                if failure is not None:
                    await send({"type": "lifespan.startup.failed", "message": failure})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send(await self._stop())
                return

    async def _start(self) -> str | None:
        """Run the startup checks, then the hooks; return a failure message."""
        try:
            self.build()
            for hook in self._startup:
                await hook()
        except StartupError as exc:
            return str(exc)
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
        return None

    async def _stop(self) -> Message:
        """Run every shutdown hook, last registered first, even after a failure."""
        failures: list[str] = []
        for hook in reversed(self._shutdown):
            try:
                await hook()
            except Exception as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
        if failures:
            return {"type": "lifespan.shutdown.failed", "message": "; ".join(failures)}
        return {"type": "lifespan.shutdown.complete"}


async def _raise_not_found(c: Context) -> Response:
    raise HTTPError(404, "Not Found")


def _raise_method_not_allowed(allow: str) -> Handler:
    async def handler(c: Context) -> Response:
        raise HTTPError(405, "Method Not Allowed", headers={"allow": allow})

    return handler
