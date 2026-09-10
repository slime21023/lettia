import re
from collections.abc import Iterable
from urllib.parse import quote

from attrs import define, field

from lettia.route import Route


@define(slots=True)
class RadixNode[HandlerT]:
    part: str
    children: dict[str, "RadixNode[HandlerT]"] = field(
        factory=dict[str, "RadixNode[HandlerT]"]
    )
    param_child: "RadixNode[HandlerT] | None" = None
    wildcard_child: "RadixNode[HandlerT] | None" = None
    routes: dict[str, Route[HandlerT]] = field(factory=dict[str, Route[HandlerT]])


class Router[HandlerT]:
    def __init__(self) -> None:
        self._static_routes: dict[tuple[str, str], Route[HandlerT]] = {}
        self._named_routes: dict[str, Route[HandlerT]] = {}
        self._parameter_specs: dict[
            tuple[str, str], tuple[tuple[int, str, bool], ...]
        ] = {}
        self._root: RadixNode[HandlerT] = RadixNode(part="")

    def add_route(
        self, method: str, path: str, handler: HandlerT, name: str | None = None
    ) -> Route[HandlerT]:
        method = method.upper()
        route = Route(method=method, path=path, handler=handler, name=name)

        if name is not None and name in self._named_routes:
            raise ValueError(f"Route name '{name}' is already registered")

        # Determine if static route (no ':' or '*')
        if ":" not in path and "*" not in path:
            if (method, path) in self._static_routes:
                raise ValueError(f"Route {method} {path} is already registered")
            if name is not None:
                self._named_routes[name] = route
            self._static_routes[(method, path)] = route
            return route

        # Otherwise insert into Radix Tree
        segments = [s for s in path.split("/") if s]
        current = self._root

        for seg in segments:
            if seg.startswith(":"):
                if current.param_child is None:
                    current.param_child = RadixNode(part=seg)
                current = current.param_child
            elif seg.startswith("*"):
                if current.wildcard_child is None:
                    current.wildcard_child = RadixNode(part=seg)
                current = current.wildcard_child
                break  # Wildcard consumes the rest of the path
            else:
                if seg not in current.children:
                    current.children[seg] = RadixNode(part=seg)
                current = current.children[seg]

        if method in current.routes:
            raise ValueError(f"Route {method} {path} is already registered")
        parameter_specs: list[tuple[int, str, bool]] = []
        for index, segment in enumerate(segments):
            if segment.startswith(":"):
                parameter_specs.append((index, segment[1:], False))
            elif segment.startswith("*"):
                parameter_specs.append((index, segment[1:] or "wildcard", True))
                break
        self._parameter_specs[(method, path)] = tuple(parameter_specs)
        if name is not None:
            self._named_routes[name] = route
        current.routes[method] = route
        return route

    def match(
        self, method: str, path: str
    ) -> tuple[Route[HandlerT], dict[str, str]] | None:
        method = method.upper()

        # 1. Check static routes (O(1))
        if (method, path) in self._static_routes:
            return self._static_routes[(method, path)], {}

        # Prefer an explicit HEAD route, then use the GET representation.
        if method == "HEAD":
            head_match = self._match_path("HEAD", path)
            if head_match is not None:
                return head_match
            return self._match_path("GET", path)

        return self._match_path(method, path)

    def _match_path(
        self, method: str, path: str
    ) -> tuple[Route[HandlerT], dict[str, str]] | None:
        # 2. Check Radix Tree (Static > Param > Wildcard)
        if (method, path) in self._static_routes:
            return self._static_routes[(method, path)], {}

        segments = [s for s in path.split("/") if s]
        return self._match_node(self._root, segments, 0, method)

    def _match_node(
        self,
        node: RadixNode[HandlerT],
        segments: list[str],
        index: int,
        method: str,
    ) -> tuple[Route[HandlerT], dict[str, str]] | None:
        if index == len(segments):
            if method in node.routes:
                route = node.routes[method]
                return route, self._path_params(route, segments)
            if node.wildcard_child is not None:
                wildcard_route = node.wildcard_child.routes.get(method)
                if wildcard_route is not None:
                    return wildcard_route, self._path_params(wildcard_route, segments)
            return None

        seg = segments[index]

        # 1. Try exact static match
        if seg in node.children:
            matched = self._match_node(node.children[seg], segments, index + 1, method)
            if matched:
                return matched

        # 2. Try parameter match (:param)
        if node.param_child is not None:
            matched = self._match_node(node.param_child, segments, index + 1, method)
            if matched:
                return matched

        # 3. Try wildcard match (*path)
        if node.wildcard_child is not None:
            wildcard_route = node.wildcard_child.routes.get(method)
            if wildcard_route is not None:
                return wildcard_route, self._path_params(wildcard_route, segments)

        return None

    def _path_params(
        self, route: Route[HandlerT], segments: list[str]
    ) -> dict[str, str]:
        params: dict[str, str] = {}
        specs = self._parameter_specs.get((route.method, route.path), ())
        for index, name, is_wildcard in specs:
            if is_wildcard:
                params[name] = "/".join(segments[index:])
                break
            if index < len(segments):
                params[name] = segments[index]
        return params

    def allowed_methods(self, path: str) -> set[str]:
        methods = {
            method for (method, route_path) in self._static_routes if route_path == path
        }
        segments = [s for s in path.split("/") if s]
        self._collect_methods(self._root, segments, 0, methods)
        methods.discard("WEBSOCKET")
        if "GET" in methods:
            methods.add("HEAD")
        return methods

    def _collect_methods(
        self,
        node: RadixNode[HandlerT],
        segments: list[str],
        index: int,
        methods: set[str],
    ) -> None:
        if index == len(segments):
            methods.update(node.routes)
            if node.wildcard_child is not None:
                methods.update(node.wildcard_child.routes)
            return

        segment = segments[index]
        child = node.children.get(segment)
        if child is not None:
            self._collect_methods(child, segments, index + 1, methods)
        if node.param_child is not None:
            self._collect_methods(node.param_child, segments, index + 1, methods)
        if node.wildcard_child is not None:
            methods.update(node.wildcard_child.routes)

    @staticmethod
    def _placeholder_tokens(path: str) -> Iterable[re.Match[str]]:
        return re.finditer(r"([:*])([A-Za-z_]\w*)", path)

    def url_for(self, name: str, **kwargs: str | int | float | bool) -> str:
        if name not in self._named_routes:
            raise KeyError(f"Route with name '{name}' not found")

        route = self._named_routes[name]
        tokens = list(self._placeholder_tokens(route.path))
        required_names = {match.group(2) for match in tokens}
        missing_names = required_names - kwargs.keys()
        if missing_names:
            missing = ", ".join(sorted(missing_names))
            raise KeyError(f"Missing route parameters: {missing}")

        extra_names = kwargs.keys() - required_names
        if extra_names:
            extra = ", ".join(sorted(extra_names))
            raise TypeError(f"Unexpected route parameters: {extra}")

        def replace(match: re.Match[str]) -> str:
            marker, parameter_name = match.groups()
            value = str(kwargs[parameter_name])
            safe = "/" if marker == "*" else ""
            return quote(value, safe=safe)

        return re.sub(r"([:*])([A-Za-z_]\w*)", replace, route.path)
