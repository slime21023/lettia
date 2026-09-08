import logging
import time
from collections.abc import Callable

from lettia.context import Context
from lettia.middleware.base import Handler, Middleware
from lettia.response import Response, normalize_response

logger: logging.Logger = logging.getLogger("lettia.access")


def request_logger(log_func: Callable[[str], None] | None = None) -> Middleware:
    log_action = log_func if log_func is not None else logger.info

    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            start_time = time.perf_counter()
            response_obj: Response | None = None

            try:
                res = await next_handler(ctx)
                response_obj = normalize_response(res)
                return response_obj
            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000
                status = response_obj.status_code if response_obj else 500
                log_message = (
                    f"{ctx.method} {ctx.path} -> {status} ({duration_ms:.2f}ms)"
                )
                log_action(log_message)

        return handler

    return middleware
