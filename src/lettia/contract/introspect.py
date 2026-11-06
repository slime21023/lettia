"""Turn a contract ``Protocol`` into operation descriptions and startup problems.

This is where startup checks 1 to 3 live: path placeholders against ``Path``
fields, the shape of the request type, and the response union. A malformed
operation is reported and left out; well-formed siblings are still returned, so
all problems of a contract are found in one pass.
"""

import collections.abc
import inspect
import re
import types
from collections.abc import Callable
from dataclasses import dataclass
from typing import (
    Annotated,
    Any,
    Literal,
    TypeVar,
    Union,
    get_args,
    get_origin,
)

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
from lettia.contract.responses import Reply, StreamReply
from lettia.core import MutableHeaders

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
    stream_item: Any | None = None


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
        shape = "(self, c, ws)" if spec.method == "WEBSOCKET" else "(self, c, req)"
        problems.append(f"the signature must be {shape}")
        return None
    try:
        hints = read_annotations(function)
    except (NameError, TypeError, AttributeError) as exc:
        problems.append(f"cannot resolve the annotations: {exc}")
        return None

    if spec.method == "WEBSOCKET":
        return _inspect_websocket(
            contract, name, function, spec, hints, parameters, problems
        )

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


def _inspect_websocket(
    contract: type,
    name: str,
    function: Callable[..., Any],
    spec: OperationSpec,
    hints: dict[str, Any],
    parameters: list[inspect.Parameter],
    problems: list[str],
) -> Operation | None:
    if "*" in spec.path:
        problems.append("contract paths cannot contain '*'")

    ret = hints.get("return")
    if ret is not None and ret is not type(None) and ret != "None":
        problems.append(
            f"the return type of a WebSocket operation must be None, got {ret!r}"
        )

    if problems:
        return None

    return Operation(
        contract=contract,
        name=name,
        spec=spec,
        operation_id=spec.operation_id or name,
        request_type=None,
        fields=(),
        responses=(),
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

    stream_base = stream_class(base)
    body_type: Any | None = None
    stream_item: Any | None = None
    headers: list[ResponseHeader] = []
    ok = stream_base is None or _check_stream_class(stream_base, problems)
    for info in msgspec.structs.fields(member):
        if info.name == "body":
            body_type = info.type
            if stream_base is not None:
                if ok:
                    stream_item = _stream_item(stream_base, body_type, problems)
                    ok = stream_item is not None
            elif isinstance(body_type, TypeVar):
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
            try:
                MutableHeaders.validated_name(header.wire_name)
            except ValueError:
                problems.append(
                    f"response field {info.name!r} of {base.__name__} declares "
                    f"the invalid header name {header.wire_name!r}"
                )
                ok = False
            else:
                headers.append(header)
    if stream_base is not None:
        ok = _check_default_headers(stream_base, headers, problems) and ok
    if not ok:
        return None
    return ResponseShape(
        base, base.status, base.media_type, body_type, tuple(headers), stream_item
    )


def stream_class(cls: type[Reply]) -> type[StreamReply[Any]] | None:
    """``cls`` as a stream response class, or ``None`` for any other reply."""
    if issubclass(cls, StreamReply):
        return cls  # pyright: ignore[reportUnknownVariableType]
    return None


def _check_stream_class(base: type[StreamReply[Any]], problems: list[str]) -> bool:
    name = base.__name__
    if base is StreamReply or base.frames is StreamReply.frames:  # pyright: ignore[reportUnknownMemberType]
        problems.append(
            f"{name} does not override frames(); a StreamReply subclass must "
            "implement it (for example use EventStream)"
        )
        return False
    status: object = getattr(base, "status", None)
    if (
        not isinstance(status, int)
        or isinstance(status, bool)
        or not 200 <= status <= 599
        or status in (204, 205, 304)
    ):
        problems.append(
            f"{name} must declare a status that allows a body (200..599, "
            f"not 204, 205 or 304), got {status!r}"
        )
        return False
    return True


def _stream_item(base: type, body_type: Any, problems: list[str]) -> Any | None:
    args = get_args(body_type)
    if get_origin(body_type) is not collections.abc.AsyncIterator or len(args) != 1:
        problems.append(
            f"{base.__name__} must declare its body as AsyncIterator[<item type>], "
            f"got {body_type!r}"
        )
        return None
    if _has_free_typevar(args[0]):
        problems.append(
            f"{base.__name__} needs a concrete item type, "
            f"e.g. {base.__name__}[Item]; got {args[0]!r}"
        )
        return None
    return args[0]


def _has_free_typevar(tp: Any) -> bool:
    if isinstance(tp, TypeVar):
        return True
    args = get_args(tp)
    if args:
        return any(_has_free_typevar(arg) for arg in args)
    # A generic class used without parameters leaves its type variables free.
    return bool(getattr(tp, "__parameters__", ())) and isinstance(tp, type)


def _check_default_headers(
    base: type[StreamReply[Any]], headers: list[ResponseHeader], problems: list[str]
) -> bool:
    name = base.__name__
    ok = True
    seen: set[str] = set()
    declared = {header.wire_name.lower() for header in headers}
    for pair in base.default_headers:
        try:
            header_name, value = pair
            MutableHeaders().add(header_name, value)
        except (TypeError, ValueError, AttributeError, UnicodeError):
            problems.append(f"{name}.default_headers has an invalid entry: {pair!r}")
            ok = False
            continue
        key = header_name.lower()
        if key in ("content-type", "content-length"):
            problems.append(f"{name}.default_headers must not set {key}")
            ok = False
        elif key in seen:
            problems.append(f"{name}.default_headers sets {key} more than once")
            ok = False
        elif key in declared:
            problems.append(
                f"{name}.default_headers sets {key}, which a HeaderName field of "
                "the same class also writes"
            )
            ok = False
        seen.add(key)
    return ok


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
