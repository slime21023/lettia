import asyncio

from lettia.context import Context
from lettia.errors import abort
from lettia.middleware.base import Handler, Middleware
from lettia.response import Response


def timeout(seconds: float) -> Middleware:
    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            deadline = asyncio.get_running_loop().time() + seconds
            current_deadline = ctx.response_deadline
            if current_deadline is None or deadline < current_deadline:
                ctx.response_deadline = deadline
            deadline_scope = asyncio.timeout(seconds)
            try:
                async with deadline_scope:
                    return await next_handler(ctx)
            except TimeoutError:
                if not deadline_scope.expired():
                    raise
                abort(504, f"Request timed out after {seconds} seconds")

        return handler

    return middleware
