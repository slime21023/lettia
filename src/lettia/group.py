from collections.abc import Callable
from typing import Any

from lettia.middleware import Middleware


class Group:
    def __init__(
        self,
        prefix: str,
        app: Any,
        middlewares: list[Middleware] | None = None,
    ) -> None:
        # Normalize prefix
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        if prefix.endswith("/") and len(prefix) > 1:
            prefix = prefix.rstrip("/")

        self.prefix = prefix
        self.app = app
        self.middlewares: list[Middleware] = list(middlewares) if middlewares else []

    def use(self, *middlewares: Middleware) -> "Group":
        self.middlewares.extend(middlewares)
        return self

    def group(self, prefix: str, *middlewares: Middleware) -> "Group":
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        combined_prefix = self.prefix.rstrip("/") + prefix
        combined_middlewares = self.middlewares + list(middlewares)
        return Group(
            prefix=combined_prefix, app=self.app, middlewares=combined_middlewares
        )

    def add_route(
        self,
        method: str,
        path: str,
        handler: Callable[..., Any],
        name: str | None = None,
        middlewares: list[Middleware] | None = None,
    ) -> None:
        if not path.startswith("/"):
            path = "/" + path
        full_path = self.prefix.rstrip("/") + path
        if full_path == "":
            full_path = "/"

        route_middlewares = self.middlewares + (
            list(middlewares) if middlewares else []
        )
        self.app.add_route(
            method, full_path, handler, name=name, middlewares=route_middlewares
        )

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
