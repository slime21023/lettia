"""``request_id``: give every request an identifier and echo it back."""

import re
import secrets
from collections.abc import Callable

from lettia.core import Context, Handler, Key, Middleware, Response

RequestId = Key[str]("request_id")
"""The id of the current request; read it with ``RequestId.get(c)``."""

# An incoming id ends up in logs and response headers, so only a conservative
# alphabet is trusted; anything else is replaced by a generated id.
_TRUSTED = re.compile(r"[A-Za-z0-9._-]{1,128}")


def _generate() -> str:
    return secrets.token_hex(8)


def request_id(
    *, header: str = "x-request-id", generate: Callable[[], str] = _generate
) -> Middleware:
    """Reuse a well-formed incoming id or generate one; store it and echo it."""

    def middleware(next_: Handler) -> Handler:
        async def run(c: Context) -> Response:
            incoming = c.request.headers.get(header)
            if incoming is not None and _TRUSTED.fullmatch(incoming):
                rid = incoming
            else:
                rid = generate()
            RequestId.set(c, rid)
            response = await next_(c)
            response.headers.set(header, rid)
            return response

        return run

    return middleware
