"""``cors``: Cross-Origin Resource Sharing."""

from collections.abc import Sequence

from lettia.core import Context, Handler, Middleware, Response, problem_response

_DEFAULT_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")
_PREFLIGHT_VARY = (
    "Origin, Access-Control-Request-Method, Access-Control-Request-Headers"
)


def cors(
    *,
    allow_origins: Sequence[str] = (),
    allow_methods: Sequence[str] = _DEFAULT_METHODS,
    allow_headers: Sequence[str] = (),
    allow_credentials: bool = False,
    expose_headers: Sequence[str] = (),
    max_age: int = 600,
) -> Middleware:
    """Answer preflights and decorate responses for allowed origins.

    Origins are compared exactly; ``"*"`` allows any origin, and
    ``allow_headers=["*"]`` reflects whatever headers the preflight asks for.
    Register it with ``app.use``: group middleware does not see preflights for
    paths that have no ``OPTIONS`` route.
    """
    any_origin = "*" in allow_origins
    if allow_credentials and any_origin:
        raise ValueError(
            "allow_credentials cannot be combined with allow_origins=['*']: "
            "browsers reject a wildcard on credentialed requests"
        )
    origins = frozenset(allow_origins)
    methods = frozenset(m.upper() for m in allow_methods)
    reflect_headers = "*" in allow_headers
    named_headers = frozenset(h.lower() for h in allow_headers)

    def decorate(response: Response, origin: str, allowed: bool) -> None:
        if not any_origin:
            response.headers.add("vary", "Origin")
        if not allowed:
            return
        response.headers.set(
            "access-control-allow-origin", "*" if any_origin else origin
        )
        if allow_credentials:
            response.headers.set("access-control-allow-credentials", "true")
        if expose_headers:
            response.headers.set(
                "access-control-expose-headers", ", ".join(expose_headers)
            )

    def answer_preflight(c: Context, origin: str, allowed: bool) -> Response:
        headers = c.request.headers
        method = headers.get("access-control-request-method", "").upper()
        requested = headers.get("access-control-request-headers")
        asked = [h.strip().lower() for h in (requested or "").split(",") if h.strip()]
        acceptable = (
            allowed
            and method in methods
            and (reflect_headers or all(h in named_headers for h in asked))
        )
        if not acceptable:
            rejected = problem_response(
                403, "Forbidden", "the CORS preflight request was rejected"
            )
            rejected.headers.add("vary", _PREFLIGHT_VARY)
            return rejected

        response = Response(204)
        response.headers.add("vary", _PREFLIGHT_VARY)
        response.headers.set(
            "access-control-allow-origin", "*" if any_origin else origin
        )
        response.headers.set("access-control-allow-methods", ", ".join(allow_methods))
        if reflect_headers and requested:
            response.headers.set("access-control-allow-headers", requested)
        elif allow_headers and not reflect_headers:
            response.headers.set(
                "access-control-allow-headers", ", ".join(allow_headers)
            )
        if allow_credentials:
            response.headers.set("access-control-allow-credentials", "true")
        response.headers.set("access-control-max-age", str(max_age))
        return response

    def middleware(next_: Handler) -> Handler:
        async def run(c: Context) -> Response:
            request = c.request
            origin = request.headers.get("origin")
            if origin is None:
                return await next_(c)
            allowed = any_origin or origin in origins
            is_preflight = (
                request.method == "OPTIONS"
                and "access-control-request-method" in request.headers
            )
            if is_preflight:
                return answer_preflight(c, origin, allowed)
            response = await next_(c)
            decorate(response, origin, allowed)
            return response

        return run

    return middleware
