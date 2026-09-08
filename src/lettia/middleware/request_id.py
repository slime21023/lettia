import uuid
from collections.abc import Callable

from lettia.context import Context
from lettia.middleware.base import Handler, Middleware
from lettia.response import Response
from lettia.state import REQUEST_ID


def request_id(
    header_name: str = "x-request-id",
    generator: Callable[[], str] | None = None,
) -> Middleware:
    id_generator = generator if generator is not None else lambda: str(uuid.uuid4())

    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            req_id = ctx.header(header_name)
            if not req_id:
                req_id = id_generator()

            ctx.state.set(REQUEST_ID, req_id)
            res = await next_handler(ctx)
            res.set_header(header_name, req_id)
            return res

        return handler

    return middleware
