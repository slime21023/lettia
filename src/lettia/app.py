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
from lettia.middleware import Handler, Middleware, build_chain
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
        "router",
        "global_middlewares",
        "pre_middlewares",
        "_error_handler",
        "_route_middlewares",
        "_compiled_chains",
        "_compiled_global_chain",
        "_compiled_pre_chain",
        "_route_list",
        "on_startup",
        "on_shutdown",
        "_is_compiled",
    )

    def __init__(self) -> None:
        self.router: Router[RouteHandler] = Router()
        self.global_middlewares: list[Middleware] = []
        self.pre_middlewares: list[Middleware] = []
        self._error_handler: ErrorHandler = default_error_handler

        self._route_middlewares: dict[tuple[str, str], list[Middleware]] = {}
        self._compiled_chains: dict[tuple[str, str], Handler] = {}
        self._compiled_global_chain: Handler | None = None
        self._compiled_pre_chain: Handler | None = None
        self._route_list: list[tuple[str, str, RouteHandler]] = []

        self.on_startup: list[LifecycleHandler] = []
        self.on_shutdown: list[LifecycleHandler] = []
        self._is_compiled: bool = False

    def use(self, *middlewares: Middleware) -> "App":
        self.global_middlewares.extend(middlewares)
        self._is_compiled = False
        return self

    def use_pre(self, *pre_middlewares: Middleware) -> "App":
        self.pre_middlewares.extend(pre_middlewares)
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
                self.on_startup.append(func)
            elif event_type == "shutdown":
                self.on_shutdown.append(func)
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
        self.router.add_route(method, path, handler, name=name)

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
            self.router.add_route("WEBSOCKET", path, func, name=name)
            self._route_list.append(("WEBSOCKET", path, func))
            self._is_compiled = False
            return func

        return decorator

    def url_for(self, name: str, **kwargs: str | int | float | bool) -> str:
        return self.router.url_for(name, **kwargs)

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

            compiled = build_chain(base_handler, route_mw)
            self._compiled_chains[(method, path)] = compiled

        async def dispatch(ctx: Context) -> Response:
            match_result = self.router.match(ctx.method, ctx.path)
            if match_result is None:
                allowed_methods = self.router.allowed_methods(ctx.path)
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

        self._compiled_global_chain = build_chain(dispatch, self.global_middlewares)
        self._compiled_pre_chain = build_chain(
            self._compiled_global_chain, self.pre_middlewares
        )

        self._is_compiled = True

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
            await writer.write(response, deadline=ctx.response_deadline)

            # Execute background tasks after response is delivered
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

        except ResponseTimeout:
            await self._write_error(
                ctx,
                writer,
                HTTPException(504, "Request timed out while writing the response"),
            )
        except Exception as exc:
            await self._write_error(ctx, writer, exc)

    async def _write_error(
        self, ctx: Context, writer: ResponseWriter, exc: Exception
    ) -> None:
        try:
            error_response = await self._error_handler(ctx, exc)
            normalized_err_resp = normalize_response(error_response)
            await writer.write(normalized_err_resp)
        except Exception:
            if not writer.committed:
                await ctx.send(
                    {
                        "type": "http.response.start",
                        "status": 500,
                        "headers": [(b"content-type", b"text/plain")],
                    }
                )
                await ctx.send(
                    {
                        "type": "http.response.body",
                        "body": b"Internal Server Error",
                    }
                )

    async def _handle_websocket(
        self, scope: WebSocketScope, receive: WebSocketReceive, send: WebSocketSend
    ) -> None:
        path = scope["path"]
        match_result = self.router.match("WEBSOCKET", path)

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
                    for handler in self.on_startup:
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
                    for handler in self.on_shutdown:
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
