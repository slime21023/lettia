import uuid
from collections.abc import Callable
from typing import Any

from lettia.context import Context
from lettia.middleware.base import Handler, Middleware
from lettia.response import normalize_response


def request_id(
    header_name: str = "x-request-id",
    generator: Callable[[], str] | None = None,
) -> Middleware:
    id_generator = generator if generator is not None else lambda: str(uuid.uuid4())

    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Any:
            req_id = ctx.header(header_name)
            if not req_id:
                req_id = id_generator()

            ctx.state["request_id"] = req_id
            res = await next_handler(ctx)
            resp = normalize_response(res)
            resp.set_header(header_name, req_id)
            return resp

        return handler

    return middleware
