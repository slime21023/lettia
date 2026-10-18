"""Compile a request type into a function that builds it from a request.

Everything that depends on the contract is worked out once, when the operation
is compiled; per request the function only reads the declared values, converts
them in one step and builds the request ``Struct``.
"""

import re
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any

import msgspec

from lettia.contract.codec import Codec, CodecError
from lettia.contract.form import UploadFile, parse_form_data
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
    head_params = [
        f for f in operation.fields if f.source in ("path", "query", "header", "cookie")
    ]
    body = next((f for f in operation.fields if f.source == "body"), None)
    form_fields = [f for f in operation.fields if f.source == "form"]
    file_fields = [f for f in operation.fields if f.source == "file"]

    params_reader = (
        _params_reader(head_params, declared, codec) if head_params else None
    )
    body_reader = _body_reader(body, codec) if body is not None else None
    form_reader = (
        _form_reader(form_fields, file_fields, declared, codec)
        if (form_fields or file_fields)
        else None
    )
    body_name = body.name if body is not None else ""

    async def decode(c: Context) -> msgspec.Struct:
        values = {} if params_reader is None else params_reader(c)
        if body_reader is not None:
            value = await body_reader(c)
            if value is not _ABSENT:
                values[body_name] = value
        if form_reader is not None:
            form_values = await form_reader(c)
            values.update(form_values)
        return request_type(**values)

    return decode


def _form_reader(
    form_fields: list[RequestField],
    file_fields: list[RequestField],
    declared: dict[str, msgspec.structs.FieldInfo],
    codec: Codec,
) -> Callable[[Context], Awaitable[dict[str, Any]]]:
    decode_form = (
        codec.build_decoder(_params_type(form_fields, declared), json=False)
        if form_fields
        else None
    )

    async def read(c: Context) -> dict[str, Any]:
        form_data, files = await parse_form_data(c.request)
        c.state["_lettia.upload_files"] = files
        try:
            values: dict[str, Any] = {}
            if decode_form is not None:
                raw_form: dict[str, str | list[str]] = {}
                for f in form_fields:
                    wire = f.wire_name
                    if wire in form_data and form_data[wire]:
                        raw_form[f.name] = (
                            form_data[wire] if f.multi else form_data[wire][0]
                        )
                try:
                    parsed_form = decode_form(raw_form)
                except CodecError as exc:
                    raise _invalid("form", exc) from None
                for f in form_fields:
                    values[f.name] = getattr(parsed_form, f.name)

            if file_fields:
                files_by_name: dict[str, list[UploadFile]] = {}
                for file in files:
                    if file.name is not None:
                        files_by_name.setdefault(file.name, []).append(file)
                for f in file_fields:
                    wire = f.wire_name
                    uploaded = files_by_name.get(wire, [])
                    if f.multi:
                        if uploaded:
                            values[f.name] = uploaded
                        elif f.required:
                            raise _invalid(
                                "file",
                                CodecError(
                                    f"missing required field `{f.name}`",
                                    f"$.{f.name}",
                                ),
                            )
                        else:
                            info = declared[f.name]
                            if info.default is not msgspec.NODEFAULT:
                                values[f.name] = info.default
                            elif callable(info.default_factory):
                                values[f.name] = info.default_factory()
                            else:
                                values[f.name] = []
                    else:
                        if uploaded:
                            values[f.name] = uploaded[0]
                        elif f.required:
                            raise _invalid(
                                "file",
                                CodecError(
                                    f"missing required field `{f.name}`",
                                    f"$.{f.name}",
                                ),
                            )
                        elif f.nullable:
                            values[f.name] = None
                        else:
                            info = declared[f.name]
                            if info.default is not msgspec.NODEFAULT:
                                values[f.name] = info.default
                            elif callable(info.default_factory):
                                values[f.name] = info.default_factory()
                            else:
                                values[f.name] = None
            return values
        except Exception:
            for file in files:
                await file.close()
            c.state.pop("_lettia.upload_files", None)
            raise

    return read


def _params_reader(
    params: list[RequestField],
    declared: dict[str, msgspec.structs.FieldInfo],
    codec: Codec,
) -> Callable[[Context], dict[str, Any]]:
    names = tuple(f.name for f in params)
    source_of = {f.name: f.source for f in params}
    extractors = [(f.name, _extractor(f)) for f in params]
    decode_params = codec.build_decoder(_params_type(params, declared), json=False)

    def read(c: Context) -> dict[str, Any]:
        raw: dict[str, str | list[str]] = {}
        for name, extract in extractors:
            found = extract(c)
            if found is not None:
                raw[name] = found
        try:
            parsed = decode_params(raw)
        except CodecError as exc:
            match = _FIELD_OF_PATH.match(exc.path)
            source = source_of.get(match.group(1), "request") if match else "request"
            raise _invalid(source, exc) from None
        return {name: getattr(parsed, name) for name in names}

    return read


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

    async def read(c: Context) -> object:
        data = await c.request.body()
        if not data:
            if not body.required:
                return _ABSENT  # the request type's default applies
            if body.nullable:
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


@lru_cache(maxsize=64)
def _media_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


@lru_cache(maxsize=64)
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
