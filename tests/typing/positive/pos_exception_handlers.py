"""Exception handlers type-check with the precise exception type."""

from lettia.core import App, Context, HTTPError, Response


async def on_key_error(c: Context, exc: KeyError) -> Response:
    return c.json(404, {"missing": exc.args[0]})


async def on_http_error(c: Context, exc: HTTPError) -> Response:
    return c.json(exc.status, {"title": exc.title})


async def on_anything(c: Context, exc: Exception) -> Response:
    return c.text(500, type(exc).__name__)


def install(app: App) -> None:
    app.add_exception_handler(KeyError, on_key_error)
    app.add_exception_handler(HTTPError, on_http_error)
    app.add_exception_handler(ValueError, on_anything)
