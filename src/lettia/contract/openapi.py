"""Derive an OpenAPI 3.1 document from mounted contracts.

The document is plain data (dicts, lists, strings, numbers) and is deterministic:
paths, methods, parameters, responses and schemas are emitted in a fixed order
that does not depend on mount order, and status descriptions come from a table
of our own because the standard library's wording differs between Python
versions.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import msgspec

from lettia.contract.codec import Codec, CodecError
from lettia.contract.guards import ApiKeyHeader, GuardSpec, HttpBearer, public
from lettia.contract.introspect import Operation, RequestField, ResponseShape
from lettia.contract.mount import MountedOperation

_REF_TEMPLATE = "#/components/schemas/{name}"
_METHOD_ORDER = ("get", "put", "post", "delete", "patch")
_LOCATION_ORDER = {"path": 0, "query": 1, "header": 2, "cookie": 3}
_DERIVED_PARTS = ("parameters", "requestBody", "responses")
_PROBLEM_MEDIA_TYPE = "application/problem+json"

_REASONS = {
    200: "OK",
    201: "Created",
    202: "Accepted",
    204: "No Content",
    301: "Moved Permanently",
    302: "Found",
    304: "Not Modified",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    410: "Gone",
    412: "Precondition Failed",
    413: "Payload Too Large",
    415: "Unsupported Media Type",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


class ValidationIssue(msgspec.Struct, rename={"location": "in"}):
    """One entry of the ``errors`` member of a 422 response."""

    location: str
    path: str
    message: str


class ProblemDetails(msgspec.Struct, kw_only=True):
    """The RFC 9457 body of the errors the framework itself produces."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None


class ValidationProblem(ProblemDetails, kw_only=True):
    errors: list[ValidationIssue] = []


@dataclass(frozen=True, slots=True)
class OpenAPIInfo:
    title: str
    version: str
    description: str | None = None


class _Slot:
    """A schema that is filled in once the codec has produced all of them."""

    __slots__ = ("default", "index")

    def __init__(self, index: int, default: object = msgspec.NODEFAULT) -> None:
        self.index = index
        self.default = default


def build_openapi(
    mounted: Sequence[MountedOperation], *, info: OpenAPIInfo, codec: Codec
) -> tuple[dict[str, Any] | None, list[str]]:
    """The document and no problems, or ``None`` and every problem found."""
    return _Builder(info, codec).build(mounted)


class _Builder:
    def __init__(self, info: OpenAPIInfo, codec: Codec) -> None:
        self._info = info
        self._codec = codec
        self._types: list[Any] = []
        self._index: dict[Any, int] = {}
        self._problems: list[str] = []
        self._security_schemes: dict[str, dict[str, Any]] = {}
        self._schemas: list[dict[str, Any]] = []

    def build(
        self, mounted: Sequence[MountedOperation]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        ordered = sorted(mounted, key=lambda m: (m.path, _method_rank(m.operation)))
        paths: dict[str, dict[str, Any]] = {}
        for item in ordered:
            method = item.operation.spec.method.lower()
            paths.setdefault(item.path, {})[method] = self._operation(item.operation)

        document: dict[str, Any] = {
            "openapi": "3.1.0",
            "info": self._info_object(),
            "paths": paths,
        }
        components = self._components()
        if components:
            document["components"] = components
        if self._problems:
            return None, self._problems
        return _resolve(document, self._schemas), []

    # --- document parts ---------------------------------------------------

    def _info_object(self) -> dict[str, str]:
        info = {"title": self._info.title, "version": self._info.version}
        if self._info.description is not None:
            info["description"] = self._info.description
        return info

    def _components(self) -> dict[str, Any]:
        components: dict[str, Any] = {}
        try:
            schemas, shared = self._codec.json_schema(
                self._types, ref_template=_REF_TEMPLATE
            )
        except CodecError as exc:
            self._problems.append(f"OpenAPI schemas: {exc.message}")
            return components
        self._schemas = schemas
        if shared:
            components["schemas"] = dict(sorted(shared.items()))
        if self._security_schemes:
            components["securitySchemes"] = dict(sorted(self._security_schemes.items()))
        return components

    def _operation(self, operation: Operation) -> dict[str, Any]:
        where = f"{operation.contract.__qualname__}.{operation.name}"
        result: dict[str, Any] = {}
        if operation.spec.tags:
            result["tags"] = list(operation.spec.tags)
        if operation.doc:
            summary, _, description = operation.doc.partition("\n")
            result["summary"] = summary.strip()
            if description.strip():
                result["description"] = description.strip()
        result["operationId"] = operation.operation_id

        parameters = self._parameters(operation.fields)
        if parameters:
            result["parameters"] = parameters
        body = next((f for f in operation.fields if f.source == "body"), None)
        if body is not None:
            result["requestBody"] = self._request_body(body)
        result["responses"] = self._responses(operation)
        security = self._security(operation.spec.guard)
        if security is not None:
            result["security"] = security

        extra = operation.spec.openapi_extra or {}
        for key in _DERIVED_PARTS:
            if key in extra:
                self._problems.append(f"{where}: openapi_extra may not set {key!r}")
        result.update({k: v for k, v in extra.items() if k not in _DERIVED_PARTS})
        return result

    # --- request ----------------------------------------------------------

    def _parameters(self, fields: Sequence[RequestField]) -> list[dict[str, Any]]:
        params = [f for f in fields if f.source != "body"]
        params.sort(key=lambda f: (_LOCATION_ORDER[f.source], f.wire_name))
        return [
            {
                "name": f.wire_name,
                "in": f.source,
                "required": f.source == "path" or f.required,
                "schema": self._slot(f.type, self._default_of(f)),
            }
            for f in params
        ]

    def _default_of(self, field: RequestField) -> object:
        if field.required or field.default is msgspec.NODEFAULT:
            return msgspec.NODEFAULT
        try:
            return json.loads(self._codec.build_encoder()(field.default))
        except (TypeError, ValueError):
            return msgspec.NODEFAULT

    def _request_body(self, body: RequestField) -> dict[str, Any]:
        return {
            "required": body.required and not body.nullable,
            "content": {"application/json": {"schema": self._slot(body.type)}},
        }

    # --- responses --------------------------------------------------------

    def _responses(self, operation: Operation) -> dict[str, Any]:
        responses: dict[int, dict[str, Any]] = {
            shape.status: self._response(shape) for shape in operation.responses
        }
        if _can_refuse(operation.spec.guard):
            for status in (401, 403):
                responses.setdefault(status, self._problem_response(status))
        if operation.fields:
            responses.setdefault(422, self._problem_response(422, ValidationProblem))
        return {str(status): responses[status] for status in sorted(responses)}

    def _problem_response(
        self, status: int, schema: type[ProblemDetails] = ProblemDetails
    ) -> dict[str, Any]:
        return {
            "description": _REASONS[status],
            "content": {_PROBLEM_MEDIA_TYPE: {"schema": self._slot(schema)}},
        }

    def _response(self, shape: ResponseShape) -> dict[str, Any]:
        result: dict[str, Any] = {"description": _REASONS.get(shape.status, "Response")}
        if shape.headers:
            result["headers"] = {
                h.wire_name: {"required": h.required, "schema": self._slot(h.type)}
                for h in sorted(shape.headers, key=lambda h: h.wire_name)
            }
        if shape.body_type is not None:
            media = shape.media_type.split(";", 1)[0].strip().lower()
            result["content"] = {media: {"schema": self._body_schema(shape, media)}}
        return result

    def _body_schema(self, shape: ResponseShape, media: str) -> object:
        is_json = media == "application/json" or media.endswith("+json")
        if not is_json and shape.body_type is bytes:
            return {"type": "string", "format": "binary"}
        if not is_json and shape.body_type is str:
            return {"type": "string"}
        return self._slot(shape.body_type)

    # --- security ---------------------------------------------------------

    def _security(self, guard: GuardSpec[Any]) -> list[dict[str, list[str]]] | None:
        for leaf in guard.leaves():
            self._register_scheme(leaf)
        if guard is public:
            return []
        alternatives = _alternatives(guard)
        return None if alternatives == [{}] else alternatives

    def _register_scheme(self, leaf: GuardSpec[Any]) -> None:
        if leaf.scheme is None or leaf is public:
            return
        if isinstance(leaf.scheme, HttpBearer):
            definition: dict[str, Any] = {"type": "http", "scheme": "bearer"}
            if leaf.scheme.format is not None:
                definition["bearerFormat"] = leaf.scheme.format
        elif isinstance(leaf.scheme, ApiKeyHeader):  # pyright: ignore[reportUnnecessaryIsInstance]
            definition = {"type": "apiKey", "in": "header", "name": leaf.scheme.name}
        else:
            self._problems.append(f"guard {leaf.name!r} has an unsupported scheme")
            return
        known = self._security_schemes.setdefault(leaf.name, definition)
        if known != definition:
            self._problems.append(
                f"security scheme {leaf.name!r} is declared by guards "
                "with different definitions"
            )

    # --- schemas ----------------------------------------------------------

    def _slot(self, tp: Any, default: object = msgspec.NODEFAULT) -> _Slot:
        try:
            index = self._index.get(tp)
        except TypeError:  # unhashable annotation: give it its own entry
            index = None
        if index is None:
            index = len(self._types)
            self._types.append(tp)
            try:
                self._index[tp] = index
            except TypeError:
                pass
        return _Slot(index, default)


def _resolve(node: Any, schemas: list[dict[str, Any]]) -> Any:
    """Replace every schema placeholder by the schema the codec produced."""
    if isinstance(node, _Slot):
        schema = dict(schemas[node.index])
        if node.default is not msgspec.NODEFAULT:
            schema["default"] = node.default
        return schema
    if isinstance(node, dict):
        mapping = cast(dict[str, Any], node)
        return {key: _resolve(value, schemas) for key, value in mapping.items()}
    if isinstance(node, list):
        # mypy narrows a list of Any here, pyright a list of Unknown.
        return [_resolve(value, schemas) for value in node]  # pyright: ignore[reportUnknownVariableType]
    return node


def _method_rank(operation: Operation) -> int:
    method = operation.spec.method.lower()
    return (
        _METHOD_ORDER.index(method) if method in _METHOD_ORDER else len(_METHOD_ORDER)
    )


def _can_refuse(guard: GuardSpec[Any]) -> bool:
    if guard.kind == "leaf":
        return guard is not public
    refusals = [_can_refuse(part) for part in guard.parts]
    return any(refusals) if guard.kind == "and" else all(refusals)


def _alternatives(guard: GuardSpec[Any]) -> list[dict[str, list[str]]]:
    """The security requirement objects a request may satisfy, one per option."""
    if guard.kind == "leaf":
        if guard is public or guard.scheme is None:
            return [{}]
        return [{guard.name: []}]
    options = [_alternatives(part) for part in guard.parts]
    combined: list[dict[str, list[str]]] = []
    if guard.kind == "or":
        for group in options:
            combined.extend(group)
    else:
        combined = [{}]
        for group in options:
            combined = [_merged(have, option) for have in combined for option in group]
    unique: list[dict[str, list[str]]] = []
    for option in combined:
        if option not in unique:
            unique.append(option)
    return unique


def _merged(
    first: dict[str, list[str]], second: dict[str, list[str]]
) -> dict[str, list[str]]:
    merged = dict(first)
    merged.update(second)
    return merged
