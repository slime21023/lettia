"""Compile a request type into a function that builds it from a request.

Everything that depends on the contract is worked out once, when the operation
is compiled; per request the function only reads the declared values, converts
them in one step and builds the request ``Struct``.
"""

import re
from collections.abc import Awaitable, Callable
from typing import Any

import msgspec
import msgspec.inspect as mi

from lettia.contract.codec import Codec, CodecError
from lettia.contract.introspect import Operation, RequestField
from lettia.core import Context, HTTPError

type RequestDecoder = Callable[[Context], Awaitable[msgspec.Struct]]
type _Extractor = Callable[[Context], str | list[str] | None]

_ABSENT = object()
_FIELD_OF_PATH = re.compile(r"^\$\.(\w+)")


def compile_request(operation: Operation, codec: Codec) -> RequestDecoder | None:
    """The decoder for ``operation``, or ``None`` when it takes no request."""
    request_type = operation.request_type
    if request_type is None:
        return None

    declared = {info.name: info for info in msgspec.structs.fields(request_type)}
    params = [f for f in operation.fields if f.source != "body"]
    body = next((f for f in operation.fields if f.source == "body"), None)

    names = tuple(f.name for f in params)
    source_of = {f.name: f.source for f in params}
    extractors = [(f.name, _extractor(f)) for f in params]
    params_type = _params_type(params, declared) if params else None
    decode_params = (
        codec.build_decoder(params_type, json=False)
        if params_type is not None
        else None
    )
    body_reader = _body_reader(body, codec) if body is not None else None
    body_name = body.name if body is not None else ""

    async def decode(c: Context) -> msgspec.Struct:
        values: dict[str, Any] = {}
        if decode_params is not None:
            raw: dict[str, str | list[str]] = {}
            for name, extract in extractors:
                found = extract(c)
                if found is not None:
                    raw[name] = found
            try:
                parsed = decode_params(raw)
            except CodecError as exc:
                match = _FIELD_OF_PATH.match(exc.path)
                source = (
                    source_of.get(match.group(1), "request") if match else "request"
                )
                raise _invalid(source, exc) from None
            for name in names:
                values[name] = getattr(parsed, name)
        if body_reader is not None:
            value = await body_reader(c)
            if value is not _ABSENT:
                values[body_name] = value
        return request_type(**values)

    return decode


def _params_type(
    params: list[RequestField], declared: dict[str, msgspec.structs.FieldInfo]
) -> type[msgspec.Struct]:
    """A Struct of the non-body fields, typed without their source markers."""
    spec: list[tuple[str, Any] | tuple[str, Any, Any]] = []
    for f in params:
        info = declared[f.name]
        if f.required:
            spec.append((f.name, f.type))
        elif info.default is not msgspec.NODEFAULT:
            spec.append((f.name, f.type, info.default))
        else:
            spec.append(
                (f.name, f.type, msgspec.field(default_factory=info.default_factory))
            )
    return msgspec.defstruct("Params", spec, kw_only=True)


def _extractor(field: RequestField) -> _Extractor:
    wire = field.wire_name
    if field.source == "path":
        return lambda c: c.path_params.get(wire)
    if field.source == "query":
        if field.multi:
            return lambda c: c.request.query.get_all(wire) or None
        return lambda c: c.request.query.get(wire)
    if field.source == "header":
        if field.multi:
            return lambda c: c.request.headers.get_all(wire) or None
        return lambda c: c.request.headers.get(wire)
    return lambda c: c.request.cookies.get(wire)


def _body_reader(
    body: RequestField, codec: Codec
) -> Callable[[Context], Awaitable[object]]:
    decode_json = codec.build_decoder(body.type, json=True)
    allows_none = _allows_none(body.type)

    async def read(c: Context) -> object:
        data = await c.request.body()
        if not data:
            if not body.required:
                return _ABSENT  # the request type's default applies
            if allows_none:
                return None
            raise _invalid("body", CodecError("the request body is required", "$"))
        content_type = c.request.headers.get("content-type")
        if content_type is not None and not _is_json(content_type):
            raise HTTPError(
                415,
                "Unsupported Media Type",
                f"expected application/json, got {_media_type(content_type)}",
            )
        try:
            return decode_json(data)
        except CodecError as exc:
            raise _invalid("body", exc) from None

    return read


def _allows_none(tp: Any) -> bool:
    info = mi.type_info(tp)
    if isinstance(info, mi.NoneType):
        return True
    return isinstance(info, mi.UnionType) and any(
        isinstance(member, mi.NoneType) for member in info.types
    )


def _media_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _is_json(content_type: str) -> bool:
    media = _media_type(content_type)
    return media == "application/json" or media.endswith("+json")


def _invalid(source: str, error: CodecError) -> HTTPError:
    path = f"$.{source}{error.path[1:]}"
    return HTTPError(
        422,
        "Unprocessable Entity",
        f"{path}: {error.message}",
        extensions={"errors": [{"in": source, "path": path, "message": error.message}]},
    )
