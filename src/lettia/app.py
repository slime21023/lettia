import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from lettia.context import Context
from lettia.errors import HTTPException, default_error_handler
from lettia.group import Group
from lettia.middleware import Handler, Middleware, build_chain
from lettia.response import ResponseWriter, normalize_response
from lettia.router import Router
from lettia.websocket import WebSocketContext, WebSocketDisconnect

logger = logging.getLogger("lettia.app")


class App:
    def __init__(self) -> None:
        self.router = Router()
        self.state: dict[str, Any] = {}
        self.global_middlewares: list[Middleware] = []
        self.pre_middlewares: list[Middleware] = []
        self._error_handler: Callable[[Context, Exception], Awaitable[Any]] = (
            default_error_handler
        )

        self._route_middlewares: dict[tuple[str, str], list[Middleware]] = {}
        self._compiled_chains: dict[tuple[str, str], Handler] = {}
        self._compiled_global_chain: Handler | None = None
        self._compiled_pre_chain: Handler | None = None
        self._route_list: list[tuple[str, str, Callable[..., Any]]] = []

        self.on_startup: list[Callable[[], Awaitable[None] | None]] = []
        self.on_shutdown: list[Callable[[], Awaitable[None] | None]] = []
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

    def set_error_handler(
        self, handler: Callable[[Context, Exception], Awaitable[Any]]
    ) -> Callable[[Context, Exception], Awaitable[Any]]:
        self._error_handler = handler
        return handler

    def error_handler(
        self, func: Callable[[Context, Exception], Awaitable[Any]]
    ) -> Callable[[Context, Exception], Awaitable[Any]]:
        self._error_handler = func
        return func

    def on_event(
        self, event_type: str
    ) -> Callable[[Callable[[], Any]], Callable[[], Any]]:
        def decorator(func: Callable[[], Any]) -> Callable[[], Any]:
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
        handler: Callable[..., Any],
        name: str | None = None,
        middlewares: list[Middleware] | None = None,
    ) -> None:
        method = method.upper()
        self.router.add_route(method, path, handler, name=name)

        route_mw = list(middlewares) if middlewares else []
        self._route_middlewares[(method, path)] = route_mw
        self._route_list.append((method, path, handler))
        self._is_compiled = False

    def get(self, path: str, name: str | None = None) -> Callable[..., Any]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.add_route("GET", path, func, name=name)
            return func

        return decorator

    def post(self, path: str, name: str | None = None) -> Callable[..., Any]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.add_route("POST", path, func, name=name)
            return func

        return decorator

    def put(self, path: str, name: str | None = None) -> Callable[..., Any]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.add_route("PUT", path, func, name=name)
            return func

        return decorator

    def delete(self, path: str, name: str | None = None) -> Callable[..., Any]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.add_route("DELETE", path, func, name=name)
            return func

        return decorator

    def patch(self, path: str, name: str | None = None) -> Callable[..., Any]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.add_route("PATCH", path, func, name=name)
            return func

        return decorator

    def websocket(self, path: str, name: str | None = None) -> Callable[..., Any]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.add_route("WEBSOCKET", path, func, name=name)
            return func

        return decorator

    def url_for(self, name: str, **kwargs: Any) -> str:
        return self.router.url_for(name, **kwargs)

    def _compile_chains(self) -> None:
        """Pre-compile handler middleware chains during boot time."""
        self._compiled_chains.clear()
        for method, path, raw_handler in self._route_list:
            route_mw = self._route_middlewares.get((method, path), [])

            async def base_handler(ctx: Context, h=raw_handler) -> Any:
                res = h(ctx)
                if isinstance(res, Awaitable):
                    return await res
                return res

            compiled = build_chain(base_handler, route_mw)
            self._compiled_chains[(method, path)] = compiled

        async def dispatch(ctx: Context) -> Any:
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

    async def __call__(
        self, scope: MutableMapping[str, Any], receive: Any, send: Any
    ) -> None:
        scope_type = scope.get("type")

        if scope_type == "lifespan":
            await self._handle_lifespan(scope, receive, send)
            return

        if scope_type == "websocket":
            await self._handle_websocket(scope, receive, send)
            return

        if scope_type != "http":
            return

        if not self._is_compiled:
            self._compile_chains()

        ctx = Context(scope=dict(scope), receive=receive, send=send)
        writer = ResponseWriter(send=send, head_only=ctx.method == "HEAD")

        try:
            if self._compiled_pre_chain is None:
                self._compile_chains()
            compiled_pre_chain = self._compiled_pre_chain
            if compiled_pre_chain is None:
                raise RuntimeError("Application middleware chain was not compiled")
            raw_response = await compiled_pre_chain(ctx)
            response = normalize_response(raw_response)
            await writer.write(response)

            # Execute background tasks after response is delivered
            for task_func, task_args, task_kwargs in ctx._background_tasks:
                try:
                    res = task_func(*task_args, **task_kwargs)
                    if isinstance(res, Awaitable):
                        await res
                except Exception:
                    logger.exception(
                        "Background task failed",
                        extra={"path": ctx.path, "method": ctx.method},
                    )

        except Exception as exc:
            try:
                error_response = await self._error_handler(ctx, exc)
                normalized_err_resp = normalize_response(error_response)
                await writer.write(normalized_err_resp)
            except Exception:
                if not writer.committed:
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 500,
                            "headers": [(b"content-type", b"text/plain")],
                        }
                    )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b"Internal Server Error",
                        }
                    )

    async def _handle_websocket(
        self, scope: MutableMapping[str, Any], receive: Any, send: Any
    ) -> None:
        path = str(scope.get("path", "/"))
        match_result = self.router.match("WEBSOCKET", path)

        if match_result is None:
            await send({"type": "websocket.close", "code": 404})
            return

        matched_route, path_params = match_result
        ws_ctx = WebSocketContext(
            scope=scope, receive=receive, send=send, path_params=path_params
        )

        try:
            res = matched_route.handler(ws_ctx)
            if isinstance(res, Awaitable):
                await res
        except WebSocketDisconnect:
            return
        except Exception:
            await ws_ctx.close(code=1011, reason="Internal Error")

    async def _handle_lifespan(
        self, scope: MutableMapping[str, Any], receive: Any, send: Any
    ) -> None:
        while True:
            message = await receive()
            msg_type = message.get("type")
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
