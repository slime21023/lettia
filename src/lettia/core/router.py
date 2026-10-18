"""Path router: a segment-level radix tree matched on the raw request path.

Priority is static > ``{parameter}`` > ``*``, with backtracking, so a request is
routed to the most specific route that can actually serve it. The raw path is
split into segments first and only parameter values are percent-decoded
afterwards, so an encoded ``%2F`` never becomes a segment boundary.
"""

from dataclasses import dataclass
from urllib.parse import quote, unquote

from lettia.core.handler import Handler, WebSocketHandler

_WILDCARD = "*"
_STATIC_SAFE = "!$&'()+,;=:@-._~%"


class RouteConflict(ValueError):
    """Two routes have the same method and the same shape."""


@dataclass(frozen=True, slots=True)
class Matched:
    handler: Handler | WebSocketHandler
    path_params: dict[str, str]


@dataclass(frozen=True, slots=True)
class MethodNotAllowed:
    """The path exists, but not for the requested method."""

    allow: tuple[str, ...]

    @property
    def allow_header(self) -> str:
        return ", ".join(self.allow)


class _Route:
    __slots__ = ("handler", "names", "pattern")

    def __init__(
        self,
        handler: Handler | WebSocketHandler,
        names: tuple[str, ...],
        pattern: str,
    ) -> None:
        self.handler = handler
        self.names = names
        self.pattern = pattern


class _Node:
    __slots__ = ("param", "routes", "static", "wildcard")

    def __init__(self) -> None:
        self.static: dict[str, _Node] = {}
        self.param: _Node | None = None
        self.routes: dict[str, _Route] = {}
        self.wildcard: dict[str, _Route] = {}


class Router:
    """Maps ``(method, path)`` to a handler and the captured path parameters."""

    def __init__(self) -> None:
        self._root = _Node()
        # Fully static routes by exact path: they outrank every other kind, so a
        # hit that serves the method needs no tree walk.
        self._exact: dict[bytes, dict[str, _Route]] = {}

    def add(self, method: str, path: str, handler: Handler | WebSocketHandler) -> None:
        method = _normalize_method(method)
        segments, names, has_wildcard = _parse(path)

        node = self._root
        for segment in segments:
            if segment is None:
                node.param = node.param or _Node()
                node = node.param
            else:
                node = node.static.setdefault(segment, _Node())

        table = node.wildcard if has_wildcard else node.routes
        existing = table.get(method)
        if existing is not None:
            raise RouteConflict(
                f"{method} {path} conflicts with the registered route "
                f"{method} {existing.pattern}"
            )
        route = _Route(handler, names, path)
        table[method] = route
        if not names and not has_wildcard:
            key = ("/" + "/".join(s for s in segments if s is not None)).encode()
            self._exact.setdefault(key, {})[method] = route

    def match(self, method: str, raw_path: bytes) -> Matched | MethodNotAllowed | None:
        """Find the route for a request; ``None`` means the path is unknown (404)."""
        exact = self._exact.get(raw_path)
        if exact is not None:
            route = _pick(exact, method)
            if route is not None:
                return Matched(route.handler, {})
        if not raw_path.startswith(b"/"):
            return None
        segments = raw_path[1:].decode("latin-1").split("/")
        allowed: set[str] = set()
        found = _walk(self._root, segments, 0, [], method, allowed)
        if found is not None:
            return found
        if allowed:
            return MethodNotAllowed(tuple(sorted(allowed)))
        return None


def _normalize_method(method: str) -> str:
    if not (method.isascii() and method.isalpha()):
        raise ValueError(f"invalid method: {method!r}")
    return method.upper()


def _parse(path: str) -> tuple[list[str | None], tuple[str, ...], bool]:
    """Split a template into static segments, ``None`` for parameters, and names."""
    if not path.startswith("/"):
        raise ValueError(f"invalid path {path!r}: it must start with '/'")
    raw_segments = path[1:].split("/")
    segments: list[str | None] = []
    names: list[str] = []
    has_wildcard = False
    for index, raw in enumerate(raw_segments):
        if raw == _WILDCARD:
            if index != len(raw_segments) - 1:
                raise ValueError(f"invalid path {path!r}: '*' must be the last segment")
            has_wildcard = True
        elif raw.startswith("{") and raw.endswith("}"):
            name = raw[1:-1]
            if not name.isidentifier():
                raise ValueError(f"invalid path {path!r}: bad parameter name {raw!r}")
            if name in names:
                raise ValueError(f"invalid path {path!r}: repeated parameter {name!r}")
            names.append(name)
            segments.append(None)
        elif any(c in raw for c in "{}*"):
            raise ValueError(
                f"invalid path {path!r}: {raw!r} mixes text with a parameter or '*'"
            )
        else:
            segments.append(quote(raw, safe=_STATIC_SAFE))
    return segments, tuple(names), has_wildcard


def _decode(value: str) -> str:
    return unquote(value, errors="replace") if "%" in value else value


def _params(route: _Route, values: list[str]) -> dict[str, str]:
    # A route is reached through exactly one segment per parameter, so the counts match.
    return dict(zip(route.names, map(_decode, values), strict=True))


def _pick(routes: dict[str, _Route], method: str) -> _Route | None:
    route = routes.get(method)
    if route is None and method == "HEAD":
        return routes.get("GET")
    return route


def _remember(allowed: set[str], routes: dict[str, _Route]) -> None:
    allowed.update(routes)
    if "GET" in routes:
        allowed.add("HEAD")


def _walk(
    node: _Node,
    segments: list[str],
    index: int,
    values: list[str],
    method: str,
    allowed: set[str],
) -> Matched | None:
    if index == len(segments):
        route = _pick(node.routes, method)
        if route is None:
            _remember(allowed, node.routes)
            return None
        return Matched(route.handler, _params(route, values))

    segment = segments[index]

    child = node.static.get(segment)
    if child is not None:
        found = _walk(child, segments, index + 1, values, method, allowed)
        if found is not None:
            return found

    if segment and node.param is not None:
        values.append(segment)
        found = _walk(node.param, segments, index + 1, values, method, allowed)
        if found is not None:
            return found
        values.pop()

    if node.wildcard:
        route = _pick(node.wildcard, method)
        if route is None:
            _remember(allowed, node.wildcard)
            return None
        params = _params(route, values)
        params[_WILDCARD] = _decode("/".join(segments[index:]))
        return Matched(route.handler, params)

    return None
