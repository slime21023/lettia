from lettia.context import Context
from lettia.middleware.base import Handler, Middleware
from lettia.response import Response


def body_limit(max_bytes: int) -> Middleware:
    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            await ctx.body(max_bytes=max_bytes)
            return await next_handler(ctx)

        return handler

    return middleware
