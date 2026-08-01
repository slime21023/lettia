import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote

from attrs import define, field

from lettia.route import Route


@define(slots=True)
class RadixNode:
    part: str
    children: dict[str, "RadixNode"] = field(factory=dict)
    param_child: "RadixNode | None" = None
    wildcard_child: "RadixNode | None" = None
    routes: dict[str, Route] = field(factory=dict)
    param_name: str | None = None
    param_names: dict[str, str] = field(factory=dict)
    wildcard_names: dict[str, str] = field(factory=dict)


class Router:
    def __init__(self) -> None:
        self._static_routes: dict[tuple[str, str], Route] = {}
        self._named_routes: dict[str, Route] = {}
        self._root = RadixNode(part="")

    def add_route(
        self, method: str, path: str, handler: Any, name: str | None = None
    ) -> Route:
        method = method.upper()
        route = Route(method=method, path=path, handler=handler, name=name)

        if name:
            self._named_routes[name] = route

        # Determine if static route (no ':' or '*')
        if ":" not in path and "*" not in path:
            self._static_routes[(method, path)] = route
            return route

        # Otherwise insert into Radix Tree
        segments = [s for s in path.split("/") if s]
        current = self._root

        for seg in segments:
            if seg.startswith(":"):
                param_name = seg[1:]
                if current.param_child is None:
                    current.param_child = RadixNode(part=seg, param_name=param_name)
                current.param_child.param_names[method] = param_name
                current = current.param_child
            elif seg.startswith("*"):
                param_name = seg[1:] if len(seg) > 1 else "wildcard"
                if current.wildcard_child is None:
                    current.wildcard_child = RadixNode(part=seg, param_name=param_name)
                current.wildcard_child.wildcard_names[method] = param_name
                current = current.wildcard_child
                break  # Wildcard consumes the rest of the path
            else:
                if seg not in current.children:
                    current.children[seg] = RadixNode(part=seg)
                current = current.children[seg]

        current.routes[method] = route
        return route

    def match(self, method: str, path: str) -> tuple[Route, dict[str, str]] | None:
        method = method.upper()

        # 1. Check static routes (O(1))
        if (method, path) in self._static_routes:
            return self._static_routes[(method, path)], {}

        # HEAD uses the GET representation when no explicit HEAD route exists.
        if method == "HEAD":
            get_match = self._match_path("GET", path)
            if get_match is not None:
                return get_match

        return self._match_path(method, path)

    def _match_path(
        self, method: str, path: str
    ) -> tuple[Route, dict[str, str]] | None:
        # 2. Check Radix Tree (Static > Param > Wildcard)
        if (method, path) in self._static_routes:
            return self._static_routes[(method, path)], {}

        segments = [s for s in path.split("/") if s]
        params: dict[str, str] = {}

        result = self._match_node(self._root, segments, 0, method, params)
        if result:
            matched_route, matched_params = result
            return matched_route, matched_params

        return None

    def _match_node(
        self,
        node: RadixNode,
        segments: list[str],
        index: int,
        method: str,
        params: dict[str, str],
    ) -> tuple[Route, dict[str, str]] | None:
        if index == len(segments):
            if method in node.routes:
                return node.routes[method], params
            return None

        seg = segments[index]

        # 1. Try exact static match
        if seg in node.children:
            matched = self._match_node(
                node.children[seg], segments, index + 1, method, params
            )
            if matched:
                return matched

        # 2. Try parameter match (:param)
        if node.param_child is not None:
            new_params = params.copy()
            parameter_name = node.param_child.param_names.get(
                method, node.param_child.param_name
            )
            if parameter_name:
                new_params[parameter_name] = seg
            matched = self._match_node(
                node.param_child, segments, index + 1, method, new_params
            )
            if matched:
                return matched

        # 3. Try wildcard match (*path)
        if node.wildcard_child is not None:
            new_params = params.copy()
            wildcard_name = (
                node.wildcard_child.wildcard_names.get(
                    method, node.wildcard_child.param_name
                )
                or "wildcard"
            )
            new_params[wildcard_name] = "/".join(segments[index:])
            if method in node.wildcard_child.routes:
                return node.wildcard_child.routes[method], new_params

        return None

    def allowed_methods(self, path: str) -> set[str]:
        methods = {
            method for (method, route_path) in self._static_routes if route_path == path
        }
        segments = [s for s in path.split("/") if s]
        self._collect_methods(self._root, segments, 0, methods)
        if "GET" in methods:
            methods.add("HEAD")
        return methods

    def _collect_methods(
        self,
        node: RadixNode,
        segments: list[str],
        index: int,
        methods: set[str],
    ) -> None:
        if index == len(segments):
            methods.update(node.routes)
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

    def url_for(self, name: str, **kwargs: Any) -> str:
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
