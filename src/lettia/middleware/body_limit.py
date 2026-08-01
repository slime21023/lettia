from typing import Any

from lettia.context import Context
from lettia.errors import abort
from lettia.middleware.base import Handler, Middleware


def body_limit(max_bytes: int) -> Middleware:
    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Any:
            content_length_hdr = ctx.header("content-length")
            if content_length_hdr is not None:
                try:
                    content_length = int(content_length_hdr)
                    if content_length < 0:
                        abort(400, "Content-Length must not be negative")
                    if content_length > max_bytes:
                        abort(
                            413,
                            f"Content-Length exceeds limit of {max_bytes} bytes",
                        )
                except ValueError:
                    abort(400, "Content-Length must be an integer")

            await ctx.body(max_bytes=max_bytes)
            return await next_handler(ctx)

        return handler

    return middleware
