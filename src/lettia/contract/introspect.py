"""Turn a contract ``Protocol`` into operation descriptions and startup problems.

This is where startup checks 1 to 3 live: path placeholders against ``Path``
fields, the shape of the request type, and the response union. A malformed
operation is reported and left out; well-formed siblings are still returned, so
all problems of a contract are found in one pass.
"""

import inspect
import re
import types
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypeVar, Union, get_args, get_origin

import msgspec
import msgspec.inspect as mi

from lettia.contract.annotations import read_annotations
from lettia.contract.markers import (
    Body,
    Cookie,
    File,
    Form,
    Header,
    HeaderName,
    Path,
    Query,
)
from lettia.contract.op import OperationSpec, operation_spec
from lettia.contract.responses import Reply

type Source = Literal["path", "query", "header", "cookie", "body", "form", "file"]

_SOURCES: dict[Any, Source] = {
    Path: "path",
    Query: "query",
    Header: "header",
    Cookie: "cookie",
    Body: "body",
    Form: "form",
    File: "file",
}
_METHODS_WITHOUT_BODY = frozenset({"GET", "HEAD", "DELETE"})
_PLACEHOLDER = re.compile(r"\{([^}]*)\}")
_MULTI_VALUED = (
    mi.ListType,
    mi.SetType,
    mi.FrozenSetType,
    mi.VarTupleType,
    mi.TupleType,
)


@dataclass(frozen=True, slots=True)
class RequestField:
    """One field of a request ``Struct`` and where its value comes from."""

    name: str
    source: Source
    type: Any
    wire_name: str
    required: bool
    multi: bool
    default: Any = msgspec.NODEFAULT
    nullable: bool = False


@dataclass(frozen=True, slots=True)
class ResponseHeader:
    """A response header taken from an ``Annotated[..., HeaderName(...)]`` field."""

    attribute: str
    wire_name: str
    type: Any
    required: bool


@dataclass(frozen=True, slots=True)
class ResponseShape:
    """One member of a response union."""

    cls: type[Reply]
    status: int
    media_type: str
    body_type: Any | None
    headers: tuple[ResponseHeader, ...]


@dataclass(frozen=True, slots=True)
class Operation:
    """A well-formed contract method, ready to be compiled."""

    contract: type
    name: str
    spec: OperationSpec
    operation_id: str
    request_type: type[msgspec.Struct] | None
    fields: tuple[RequestField, ...]
    responses: tuple[ResponseShape, ...]
    doc: str | None


def inspect_contract(contract: type) -> tuple[list[Operation], list[str]]:
    """Describe every ``op`` method defined directly on ``contract``."""
    operations: list[Operation] = []
    problems: list[str] = []
    for name, member in vars(contract).items():
        spec = operation_spec(member)
        if spec is None:
            continue
        found: list[str] = []
        operation = _inspect(contract, name, member, spec, found)
        if found:
            problems.extend(f"{contract.__qualname__}.{name}: {p}" for p in found)
        elif operation is not None:
            operations.append(operation)
    return operations, problems


def _inspect(
    contract: type,
    name: str,
    function: Callable[..., Any],
    spec: OperationSpec,
    problems: list[str],
) -> Operation | None:
    parameters = list(inspect.signature(function).parameters.values())
    if len(parameters) != 3 or any(
        p.kind is not p.POSITIONAL_OR_KEYWORD for p in parameters
    ):
        problems.append("the signature must be (self, c, req)")
        return None
    try:
        hints = read_annotations(function)
    except (NameError, TypeError, AttributeError) as exc:
        problems.append(f"cannot resolve the annotations: {exc}")
        return None

    request_type, fields = _inspect_request(spec, hints, parameters[2].name, problems)
    responses = _inspect_responses(hints, problems)
    if problems:
        return None
    return Operation(
        contract=contract,
        name=name,
        spec=spec,
        operation_id=spec.operation_id or name,
        request_type=request_type,
        fields=fields,
        responses=responses,
        doc=inspect.getdoc(function),
    )


# --- request ------------------------------------------------------------


def _inspect_request(
    spec: OperationSpec, hints: dict[str, Any], param: str, problems: list[str]
) -> tuple[type[msgspec.Struct] | None, tuple[RequestField, ...]]:
    if "*" in spec.path:
        problems.append("contract paths cannot contain '*': OpenAPI cannot describe it")

    if param not in hints:
        problems.append(f"the request parameter {param!r} has no annotation")
        return None, ()
    hint = hints[param]

    fields: list[RequestField] = []
    request_type: type[msgspec.Struct] | None = None
    if hint is type(None):
        pass
    elif isinstance(hint, type) and issubclass(hint, msgspec.Struct):
        request_type = hint
        fields = _inspect_fields(hint, problems)
    else:
        problems.append(
            f"the request type must be a msgspec Struct or None, got {hint!r}"
        )

    _check_path_fields(spec, fields, problems)
    _check_body_fields(spec, fields, problems)
    return request_type, tuple(fields)


def _inspect_fields(
    request_type: type[msgspec.Struct], problems: list[str]
) -> list[RequestField]:
    fields: list[RequestField] = []
    for info in msgspec.structs.fields(request_type):
        source = _SOURCES.get(get_origin(info.type))
        if source is None:
            problems.append(_unmarked(info.name, info.type))
            continue
        inner = get_args(info.type)[0]
        wire_name = info.name
        if source == "header":
            inner, wire_name = _header_name(inner, info.name)
        try:
            multi = _is_multi_valued(inner)
            nullable = _is_nullable(inner)
        except TypeError as exc:
            problems.append(
                f"field {info.name!r} has a type msgspec cannot handle: {exc}"
            )
            continue
        fields.append(
            RequestField(
                info.name,
                source,
                inner,
                wire_name,
                info.required,
                multi,
                default=info.default,
                nullable=nullable,
            )
        )
    return fields


def _unmarked(name: str, hint: Any) -> str:
    message = (
        f"field {name!r} has no source marker; "
        "mark it with Path[T], Query[T], Header[T], Cookie[T], Body[T], "
        "Form[T] or File[T]"
    )
    members = get_args(hint) if get_origin(hint) in (Union, types.UnionType) else ()
    if any(get_origin(m) in _SOURCES for m in members):
        message += (
            "; to make it optional wrap the marker's argument, e.g. Body[X | None]"
        )
    return message


def _header_name(inner: Any, field_name: str) -> tuple[Any, str]:
    default = field_name.replace("_", "-")
    if get_origin(inner) is not Annotated:
        return inner, default
    base, *metadata = get_args(inner)
    for item in metadata:
        if isinstance(item, HeaderName):
            return base, item.name
    return base, default


def _is_multi_valued(tp: Any) -> bool:
    def check(info: mi.Type) -> bool:
        if isinstance(info, _MULTI_VALUED):
            return True
        if isinstance(info, mi.UnionType):
            return any(check(member) for member in info.types)
        return False

    return check(mi.type_info(tp))


def _is_nullable(tp: Any) -> bool:
    info = mi.type_info(tp)
    if isinstance(info, mi.NoneType):
        return True
    return isinstance(info, mi.UnionType) and any(
        isinstance(member, mi.NoneType) for member in info.types
    )


def _check_path_fields(
    spec: OperationSpec, fields: list[RequestField], problems: list[str]
) -> None:
    placeholders = {m.group(1) for m in _PLACEHOLDER.finditer(spec.path)}
    path_fields = {f.name for f in fields if f.source == "path"}
    for missing in sorted(placeholders - path_fields):
        problems.append(
            f"path {spec.path!r} has the placeholder '{{{missing}}}' "
            "but the request has no matching Path field"
        )
    for extra in sorted(path_fields - placeholders):
        problems.append(
            f"Path field {extra!r} has no '{{{extra}}}' placeholder in {spec.path!r}"
        )


def _check_body_fields(
    spec: OperationSpec, fields: list[RequestField], problems: list[str]
) -> None:
    bodies = [f.name for f in fields if f.source == "body"]
    form_fields = [f.name for f in fields if f.source in ("form", "file")]
    if len(bodies) > 1:
        problems.append(f"a request can have at most one Body field, found {bodies}")
    if bodies and form_fields:
        problems.append(
            f"cannot mix Body fields {bodies} with Form/File fields {form_fields}"
        )
    if bodies and spec.method in _METHODS_WITHOUT_BODY:
        problems.append(f"{spec.method} operations cannot have a Body field")
    if form_fields and spec.method in _METHODS_WITHOUT_BODY:
        problems.append(f"{spec.method} operations cannot have Form or File fields")


# --- responses ----------------------------------------------------------


def _inspect_responses(
    hints: dict[str, Any], problems: list[str]
) -> tuple[ResponseShape, ...]:
    if "return" not in hints:
        problems.append(
            "the return annotation is missing; declare the response classes"
        )
        return ()
    shapes = [
        shape
        for member in _union_members(hints["return"])
        if (shape := _inspect_response_shape(member, problems)) is not None
    ]
    seen: set[int] = set()
    for shape in shapes:
        if shape.status in seen:
            problems.append(
                f"status {shape.status} is declared more than once; "
                "each status code may appear once in the response union"
            )
        seen.add(shape.status)
    return tuple(shapes)


def _union_members(hint: Any) -> tuple[Any, ...]:
    if get_origin(hint) in (Union, types.UnionType):
        return get_args(hint)
    return (hint,)


def _inspect_response_shape(member: Any, problems: list[str]) -> ResponseShape | None:
    base = get_origin(member) or member
    if not (isinstance(base, type) and issubclass(base, Reply) and base is not Reply):
        name = getattr(member, "__name__", repr(member))
        problems.append(f"{name} is not a response class (a subclass of Reply)")
        return None

    body_type: Any | None = None
    headers: list[ResponseHeader] = []
    ok = True
    for info in msgspec.structs.fields(member):
        if info.name == "body":
            body_type = info.type
            if isinstance(body_type, TypeVar):
                problems.append(
                    f"{base.__name__} needs a body type, e.g. {base.__name__}[T]"
                )
                ok = False
            continue
        header = _response_header(info.name, info.type)
        if header is None:
            problems.append(
                f"response field {info.name!r} of {base.__name__} must be 'body' "
                "or an Annotated[str, HeaderName(...)] header"
            )
            ok = False
        else:
            headers.append(header)
    if not ok:
        return None
    return ResponseShape(base, base.status, base.media_type, body_type, tuple(headers))


def _response_header(attribute: str, hint: Any) -> ResponseHeader | None:
    if get_origin(hint) is not Annotated:
        return None
    base, *metadata = get_args(hint)
    for item in metadata:
        if isinstance(item, HeaderName):
            required = not _is_nullable(base)
            return ResponseHeader(attribute, item.name, _without_none(base), required)
    return None


def _without_none(tp: Any) -> Any:
    """``str | None`` becomes ``str``; any other type is returned as is."""
    if get_origin(tp) not in (Union, types.UnionType):
        return tp
    rest = tuple(member for member in get_args(tp) if member is not type(None))
    joined = rest[0]
    for member in rest[1:]:
        joined = joined | member
    return joined
