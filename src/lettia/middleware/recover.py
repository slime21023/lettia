import logging

from lettia.context import Context
from lettia.errors import HTTPException
from lettia.middleware.base import Handler, Middleware
from lettia.response import Response

logger: logging.Logger = logging.getLogger("lettia.recover")


def recover() -> Middleware:
    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            try:
                return await next_handler(ctx)
            except HTTPException:
                raise
            except Exception:
                logger.exception("Unhandled exception recovered")
                raise

        return handler

    return middleware
