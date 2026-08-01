import logging
from collections.abc import Callable
from typing import Any

from lettia.context import Context
from lettia.errors import HTTPException
from lettia.middleware.base import Handler, Middleware
from lettia.response import TextResponse, normalize_response

logger = logging.getLogger("lettia.recover")


def recover(
    on_recover: Callable[[Context, Exception], Any] | None = None,
) -> Middleware:
    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Any:
            try:
                return await next_handler(ctx)
            except HTTPException:
                raise
            except Exception as exc:
                logger.exception("Unhandled exception recovered")
                if on_recover is not None:
                    res = on_recover(ctx, exc)
                    if hasattr(res, "__await__"):
                        res = await res
                    return normalize_response(res)
                return TextResponse("Internal Server Error", status_code=500)

        return handler

    return middleware
