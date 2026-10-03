from collections.abc import Sequence

from lettia.context import Context
from lettia.middleware.base import Handler, Middleware
from lettia.response import Response, normalize_response


def _merge_vary(response: Response, *names: str) -> None:
    tokens: dict[str, str] = {}
    for name, value in response.headers.items():
        if name.lower() == "vary":
            for token in value.split(","):
                if token := token.strip():
                    tokens.setdefault(token.lower(), token)
    for name in names:
        tokens.setdefault(name.lower(), name)
    response.set_header("vary", "*" if "*" in tokens else ", ".join(tokens.values()))


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
    methods_str = ", ".join(method.upper() for method in allow_methods)
    headers_str = ", ".join(allow_headers)
    wildcard_methods = "*" in allow_methods
    wildcard_headers = "*" in allow_headers

    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            origin = ctx.header("origin")
            requested_method = ctx.header("access-control-request-method")
            preflight = ctx.method == "OPTIONS" and bool(origin and requested_method)
            resp = (
                Response(status_code=204)
                if preflight
                else normalize_response(await next_handler(ctx))
            )

            def finalize(response: Response) -> None:
                vary: list[str] = []
                if "*" not in origins_set:
                    vary.append("Origin")
                if preflight and wildcard_headers:
                    vary.append("Access-Control-Request-Headers")
                if preflight and wildcard_methods:
                    vary.append("Access-Control-Request-Method")
                if vary:
                    _merge_vary(response, *vary)

                if not origin or ("*" not in origins_set and origin not in origins_set):
                    return

                response.set_header(
                    "access-control-allow-origin", "*" if "*" in origins_set else origin
                )
                if allow_credentials:
                    response.set_header("access-control-allow-credentials", "true")

                methods = methods_str
                headers = headers_str
                if preflight:
                    if wildcard_methods and requested_method:
                        methods = requested_method.strip().upper()
                    if wildcard_headers:
                        requested_headers = (
                            ctx.header("access-control-request-headers") or ""
                        )
                        headers = ", ".join(
                            dict.fromkeys(
                                name.strip().lower()
                                for name in requested_headers.split(",")
                                if name.strip()
                            )
                        )
                response.set_header("access-control-allow-methods", methods)
                response.set_header("access-control-allow-headers", headers)
                if max_age > 0:
                    response.set_header("access-control-max-age", str(max_age))

            ctx._register_response_finalizer(resp, finalize)  # pyright: ignore[reportPrivateUsage]
            return resp

        return handler

    return middleware
