from collections.abc import Sequence

from lettia.context import Context
from lettia.middleware.base import Handler, Middleware
from lettia.response import Response, normalize_response


def cors(
    allow_origins: Sequence[str] = ("*",),
    allow_methods: Sequence[str] = ("GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"),
    allow_headers: Sequence[str] = ("*",),
    allow_credentials: bool = False,
    max_age: int = 600,
) -> Middleware:
    origins_set = set(allow_origins)
    if allow_credentials and "*" in origins_set:
        raise ValueError("allow_credentials cannot be used with wildcard origins")
    methods_str = ", ".join(allow_methods)
    headers_str = ", ".join(allow_headers)

    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            origin = ctx.header("origin")

            # Helper to apply CORS headers
            def apply_cors_headers(resp: Response, req_origin: str | None) -> None:
                if not req_origin:
                    return

                if "*" in origins_set:
                    resp.set_header("access-control-allow-origin", "*")
                elif req_origin in origins_set:
                    resp.set_header("access-control-allow-origin", req_origin)
                    resp.set_header("vary", "Origin")

                if allow_credentials and "*" not in origins_set:
                    resp.set_header("access-control-allow-credentials", "true")

                resp.set_header("access-control-allow-methods", methods_str)
                resp.set_header("access-control-allow-headers", headers_str)
                if max_age > 0:
                    resp.set_header("access-control-max-age", str(max_age))

            # Pre-flight OPTIONS request
            if ctx.method == "OPTIONS" and origin:
                preflight_resp = Response(status_code=204)
                apply_cors_headers(preflight_resp, origin)
                return preflight_resp

            res = await next_handler(ctx)
            resp = normalize_response(res)
            apply_cors_headers(resp, origin)
            return resp

        return handler

    return middleware
