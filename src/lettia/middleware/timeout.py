import asyncio
from typing import Any

from lettia.context import Context
from lettia.errors import abort
from lettia.middleware.base import Handler, Middleware


def timeout(seconds: float) -> Middleware:
    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Any:
            try:
                async with asyncio.timeout(seconds):
                    return await next_handler(ctx)
            except TimeoutError:
                abort(504, f"Request timed out after {seconds} seconds")

        return handler

    return middleware
