"""Compose an application with explicit, testable dependencies."""

from collections.abc import Awaitable, Callable

from attrs import define

from lettia import App, Context, HTTPHandler, LifecycleHandler


@define(slots=True)
class Services:
    """Application-owned dependencies, not framework state."""

    get_user_name: Callable[[str], Awaitable[str | None]]
    start: Callable[[], Awaitable[None]]
    close: Callable[[], Awaitable[None]]


def register_routes(app: App, services: Services) -> None:
    async def get_user(ctx: Context) -> dict[str, str]:
        user_id = ctx.path_params["user_id"]
        name = await services.get_user_name(user_id)
        if name is None:
            ctx.abort(404, "User not found")
        return {"id": user_id, "name": name}

    handler: HTTPHandler = get_user
    app.add_route("GET", "/users/:user_id", handler)


def create_app(services: Services) -> App:
    app = App()

    async def startup() -> None:
        await services.start()

    async def shutdown() -> None:
        await services.close()

    startup_handler: LifecycleHandler = startup
    shutdown_handler: LifecycleHandler = shutdown
    app.on_event("startup")(startup_handler)
    app.on_event("shutdown")(shutdown_handler)

    register_routes(app, services)
    return app
