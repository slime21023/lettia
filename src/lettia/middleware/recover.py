import logging
from collections.abc import Awaitable, Callable

from lettia.context import Context
from lettia.errors import HTTPException
from lettia.middleware.base import Handler, Middleware
from lettia.response import Response, ResponseValue, TextResponse, normalize_response

logger: logging.Logger = logging.getLogger("lettia.recover")


def recover(
    on_recover: (
        Callable[[Context, Exception], ResponseValue | Awaitable[ResponseValue]] | None
    ) = None,
) -> Middleware:
    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            try:
                return await next_handler(ctx)
            except HTTPException:
                raise
            except Exception as exc:
                logger.exception("Unhandled exception recovered")
                if on_recover is not None:
                    res = on_recover(ctx, exc)
                    if isinstance(res, Awaitable):
                        res = await res
                    return normalize_response(res)
                return TextResponse("Internal Server Error", status_code=500)

        return handler

    return middleware
