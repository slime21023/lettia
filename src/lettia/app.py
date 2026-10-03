import logging
from collections.abc import Awaitable, Callable
from typing import Literal, cast, overload

from lettia.asgi import (
    ASGIScope,
    HTTPReceive,
    HTTPScope,
    HTTPSend,
    LifespanReceive,
    LifespanScope,
    LifespanSend,
    WebSocketReceive,
    WebSocketScope,
    WebSocketSend,
    _route_path,  # pyright: ignore[reportPrivateUsage]
)
from lettia.context import Context
from lettia.errors import HTTPException, default_error_handler
from lettia.group import Group
from lettia.handlers import (
    ErrorHandler,
    HTTPDecorator,
    HTTPHandler,
    LifecycleHandler,
    RouteHandler,
    WebSocketHandler,
)
from lettia.middleware import Handler, Middleware
from lettia.response import (
    Response,
    ResponseTimeout,
    ResponseWriter,
    normalize_response,
)
from lettia.router import Router
from lettia.websocket import WebSocketContext, WebSocketDisconnect

logger: logging.Logger = logging.getLogger("lettia.app")


class App:
    __slots__ = (
        "_router",
        "_global_middlewares",
        "_pre_middlewares",
        "_error_handler",
        "_route_middlewares",
        "_compiled_chains",
        "_compiled_pre_chain",
        "_route_list",
        "_on_startup",
        "_on_shutdown",
        "_is_compiled",
    )

    def __init__(self) -> None:
        self._router: Router[RouteHandler] = Router()
        self._global_middlewares: list[Middleware] = []
        self._pre_middlewares: list[Middleware] = []
        self._error_handler: ErrorHandler = default_error_handler

        self._route_middlewares: dict[tuple[str, str], list[Middleware]] = {}
        self._compiled_chains: dict[tuple[str, str], Handler] = {}
        self._compiled_pre_chain: Handler | None = None
        self._route_list: list[tuple[str, str, RouteHandler]] = []

        self._on_startup: list[LifecycleHandler] = []
        self._on_shutdown: list[LifecycleHandler] = []
        self._is_compiled: bool = False

    def use(self, *middlewares: Middleware) -> "App":
        self._global_middlewares.extend(middlewares)
        self._is_compiled = False
        return self

    def use_pre(self, *pre_middlewares: Middleware) -> "App":
        self._pre_middlewares.extend(pre_middlewares)
        self._is_compiled = False
        return self

    def group(self, prefix: str, *middlewares: Middleware) -> Group:
        return Group(prefix=prefix, app=self, middlewares=list(middlewares))

    def set_error_handler(self, handler: ErrorHandler) -> ErrorHandler:
        self._error_handler = handler
        return handler

    def error_handler(self, func: ErrorHandler) -> ErrorHandler:
        self._error_handler = func
        return func

    def on_event(
        self, event_type: Literal["startup", "shutdown"]
    ) -> Callable[[LifecycleHandler], LifecycleHandler]:
        def decorator(func: LifecycleHandler) -> LifecycleHandler:
            if event_type == "startup":
                self._on_startup.append(func)
            elif event_type == "shutdown":
                self._on_shutdown.append(func)
            else:
                raise ValueError(f"Unknown event type: {event_type}")
            return func

        return decorator

    def add_route(
        self,
        method: str,
        path: str,
        handler: HTTPHandler,
        name: str | None = None,
        middlewares: list[Middleware] | None = None,
    ) -> None:
        method = method.upper()
        self._router.add_route(method, path, handler, name=name)

        route_mw = list(middlewares) if middlewares else []
        self._route_middlewares[(method, path)] = route_mw
        self._route_list.append((method, path, handler))
        self._is_compiled = False

    def get(self, path: str, name: str | None = None) -> HTTPDecorator:
        def decorator(func: HTTPHandler) -> HTTPHandler:
            self.add_route("GET", path, func, name=name)
            return func

        return decorator

    def post(self, path: str, name: str | None = None) -> HTTPDecorator:
        def decorator(func: HTTPHandler) -> HTTPHandler:
            self.add_route("POST", path, func, name=name)
            return func

        return decorator

    def put(self, path: str, name: str | None = None) -> HTTPDecorator:
        def decorator(func: HTTPHandler) -> HTTPHandler:
            self.add_route("PUT", path, func, name=name)
            return func

        return decorator

    def delete(self, path: str, name: str | None = None) -> HTTPDecorator:
        def decorator(func: HTTPHandler) -> HTTPHandler:
            self.add_route("DELETE", path, func, name=name)
            return func

        return decorator

    def patch(self, path: str, name: str | None = None) -> HTTPDecorator:
        def decorator(func: HTTPHandler) -> HTTPHandler:
            self.add_route("PATCH", path, func, name=name)
            return func

        return decorator

    def websocket(
        self, path: str, name: str | None = None
    ) -> Callable[[WebSocketHandler], WebSocketHandler]:
        def decorator(func: WebSocketHandler) -> WebSocketHandler:
            self._router.add_route("WEBSOCKET", path, func, name=name)
            self._route_list.append(("WEBSOCKET", path, func))
            self._is_compiled = False
            return func

        return decorator

    def url_for(self, name: str, **kwargs: str | int | float | bool) -> str:
        return self._router.url_for(name, **kwargs)

    def _compile_chains(self) -> None:
        """Pre-compile handler middleware chains during boot time."""
        self._compiled_chains.clear()
        for method, path, raw_handler in self._route_list:
            if method == "WEBSOCKET":
                continue
            route_mw = self._route_middlewares.get((method, path), [])
            typed_handler = cast(HTTPHandler, raw_handler)

            async def base_handler(
                ctx: Context, h: HTTPHandler = typed_handler
            ) -> Response:
                res = h(ctx)
                if isinstance(res, Awaitable):
                    return normalize_response(await res)
                return normalize_response(res)

            compiled = self._build_chain(base_handler, route_mw)
            self._compiled_chains[(method, path)] = compiled

        async def dispatch(ctx: Context) -> Response:
            path = _route_path(ctx.scope)
            match_result = self._router.match(ctx.method, path)
            if match_result is None:
                allowed_methods = self._router.allowed_methods(path)
                if allowed_methods:
                    raise HTTPException(
                        405,
                        "Method Not Allowed",
                        headers={"Allow": ", ".join(sorted(allowed_methods))},
                    )
                raise HTTPException(404, "Not Found")

            matched_route, path_params = match_result
            ctx.path_params = path_params
            compiled_chain = self._compiled_chains.get(
                (matched_route.method, matched_route.path)
            )
            if compiled_chain is None:
                raise RuntimeError(
                    f"Route chain was not compiled: {matched_route.method} "
                    f"{matched_route.path}"
                )
            return await compiled_chain(ctx)

        global_chain = self._build_chain(dispatch, self._global_middlewares)
        self._compiled_pre_chain = self._build_chain(
            global_chain, self._pre_middlewares
        )

        self._is_compiled = True

    def _protect(self, handler: Handler) -> Handler:
        async def protected(ctx: Context) -> Response:
            try:
                return await handler(ctx)
            except Exception as exc:
                return await self._render_error(ctx, exc)

        return protected

    def _build_chain(self, handler: Handler, middlewares: list[Middleware]) -> Handler:
        chain = self._protect(handler)
        for middleware in reversed(middlewares):
            chain = self._protect(middleware(chain))
        return chain

    @overload
    async def __call__(
        self, scope: HTTPScope, receive: HTTPReceive, send: HTTPSend
    ) -> None: ...

    @overload
    async def __call__(
        self, scope: WebSocketScope, receive: WebSocketReceive, send: WebSocketSend
    ) -> None: ...

    @overload
    async def __call__(
        self, scope: LifespanScope, receive: LifespanReceive, send: LifespanSend
    ) -> None: ...

    async def __call__(self, scope: ASGIScope, receive: object, send: object) -> None:
        if scope["type"] == "lifespan":
            await self._handle_lifespan(
                scope, cast(LifespanReceive, receive), cast(LifespanSend, send)
            )
            return

        if scope["type"] == "websocket":
            await self._handle_websocket(
                scope, cast(WebSocketReceive, receive), cast(WebSocketSend, send)
            )
            return

        if not self._is_compiled:
            self._compile_chains()

        ctx = Context(
            scope=scope, receive=cast(HTTPReceive, receive), send=cast(HTTPSend, send)
        )
        ctx._defer_response_policies()  # pyright: ignore[reportPrivateUsage]
        writer = ResponseWriter(
            send=cast(HTTPSend, send), head_only=ctx.method == "HEAD"
        )

        try:
            if self._compiled_pre_chain is None:
                self._compile_chains()
            compiled_pre_chain = self._compiled_pre_chain
            if compiled_pre_chain is None:
                raise RuntimeError("Application middleware chain was not compiled")
            raw_response = await compiled_pre_chain(ctx)
            response = normalize_response(raw_response)
            completed = await self._write_response(ctx, writer, response)
        except ResponseTimeout:
            completed = await self._write_error(
                ctx,
                writer,
                HTTPException(504, "Request timed out while writing the response"),
            )
        except Exception as exc:
            completed = await self._write_error(ctx, writer, exc)

        if not completed:
            return
        # Normal and replacement responses share the same completion path.
        for task_func, task_args, task_kwargs in ctx.background_tasks:
            try:
                res = task_func(*task_args, **task_kwargs)
                if isinstance(res, Awaitable):
                    await res
            except Exception:
                logger.exception(
                    "Background task failed",
                    extra={"path": ctx.path, "method": ctx.method},
                )

    async def _write_response(
        self, ctx: Context, writer: ResponseWriter, response: Response
    ) -> bool:
        return await writer._deliver(  # pyright: ignore[reportPrivateUsage]
            response,
            ctx.response_deadline,
            ctx._finalize_response,  # pyright: ignore[reportPrivateUsage]
            ctx._wait_for_disconnect,  # pyright: ignore[reportPrivateUsage]
        )

    async def _write_error(
        self, ctx: Context, writer: ResponseWriter, exc: Exception
    ) -> bool:
        if not writer._can_replace_response:  # pyright: ignore[reportPrivateUsage]
            logger.error(
                "Response failed after start was attempted",
                exc_info=exc,
                extra={"path": ctx.path, "method": ctx.method},
            )
            return False
        ctx.response_deadline = None
        try:
            response = await self._render_error(ctx, exc)
            return await self._write_response(ctx, writer, response)
        except Exception:
            if not writer._can_replace_response:  # pyright: ignore[reportPrivateUsage]
                logger.exception(
                    "Error response failed after start was attempted",
                    extra={"path": ctx.path, "method": ctx.method},
                )
                return False
            response = Response(status_code=500, body=b"Internal Server Error")
            return await self._write_response(ctx, writer, response)

    async def _render_error(self, ctx: Context, exc: Exception) -> Response:
        if not isinstance(exc, HTTPException):
            logger.exception(
                "Request failed",
                exc_info=exc,
                extra={"path": ctx.path, "method": ctx.method},
            )
        try:
            error_response = self._error_handler(ctx, exc)
            if isinstance(error_response, Awaitable):
                error_response = await error_response
            return normalize_response(error_response)
        except Exception:
            logger.exception("Error handler failed")
            return normalize_response(await default_error_handler(ctx, exc))

    async def _handle_websocket(
        self, scope: WebSocketScope, receive: WebSocketReceive, send: WebSocketSend
    ) -> None:
        initial_message = await receive()
        if initial_message["type"] == "websocket.disconnect":
            return
        if initial_message["type"] != "websocket.connect":
            await send({"type": "websocket.close", "code": 1002})
            return

        path = _route_path(scope)
        match_result = self._router.match("WEBSOCKET", path)

        if match_result is None:
            await send({"type": "websocket.close", "code": 404})
            return

        matched_route, path_params = match_result
        ws_ctx = WebSocketContext(
            scope=scope,
            receive=receive,
            send=send,
            path_params=path_params,
        )

        try:
            handler = cast(WebSocketHandler, matched_route.handler)
            await handler(ws_ctx)
        except WebSocketDisconnect:
            return
        except Exception:
            await ws_ctx.close(code=1011, reason="Internal Error")

    async def _handle_lifespan(
        self, scope: LifespanScope, receive: LifespanReceive, send: LifespanSend
    ) -> None:
        while True:
            message = await receive()
            msg_type = message["type"]
            if msg_type == "lifespan.startup":
                try:
                    self._compile_chains()
                    for handler in self._on_startup:
                        res = handler()
                        if isinstance(res, Awaitable):
                            await res
                    await send({"type": "lifespan.startup.complete"})
                except Exception as exc:
                    await send(
                        {
                            "type": "lifespan.startup.failed",
                            "message": str(exc),
                        }
                    )
                    return
            elif msg_type == "lifespan.shutdown":
                try:
                    for handler in self._on_shutdown:
                        res = handler()
                        if isinstance(res, Awaitable):
                            await res
                    await send({"type": "lifespan.shutdown.complete"})
                except Exception as exc:
                    await send(
                        {
                            "type": "lifespan.shutdown.failed",
                            "message": str(exc),
                        }
                    )
                return
