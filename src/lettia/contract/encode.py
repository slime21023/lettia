"""Compile an operation's response union into a function that builds a Response."""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, NamedTuple

from lettia.contract.codec import Codec, CodecError
from lettia.contract.introspect import Operation, ResponseShape, stream_class
from lettia.contract.responses import Reply
from lettia.core import MutableHeaders, Response

type ResponseEncoder = Callable[[Reply], Response]


class _HeaderField(NamedTuple):
    attribute: str  # on the response class
    name: bytes  # lowercase wire name, validated when the route is compiled
    required: bool


@dataclass(frozen=True, slots=True)
class _Plan:
    status: int
    media_type: str
    has_body: bool
    json_body: bool
    content_type: tuple[bytes, bytes]
    header_fields: tuple[_HeaderField, ...]
    check: Callable[[bytes], Any] | None
    declared: str
    frames: Callable[..., AsyncIterator[bytes]] | None = None
    stream_headers: tuple[tuple[bytes, bytes], ...] = ()
    watch_disconnect: bool = False
    item_check: Callable[[object], None] | None = None


def _plan(
    shape: ResponseShape,
    codec: Codec,
    encode_json: Callable[[object], bytes],
    operation: Operation,
    *,
    debug: bool,
) -> _Plan:
    media = shape.media_type.split(";", 1)[0].strip().lower()
    json_body = media == "application/json" or media.endswith("+json")
    media_type = shape.media_type
    if (
        media.startswith("text/")
        and shape.body_type is str
        and "charset" not in media_type.lower()
    ):
        media_type = f"{media_type}; charset=utf-8"  # str bodies are sent as UTF-8
    check = None
    if (
        debug
        and json_body
        and shape.stream_item is None
        and shape.body_type is not None
    ):
        check = codec.build_decoder(shape.body_type, json=True)
    frames: Callable[..., AsyncIterator[bytes]] | None = None
    stream_headers: tuple[tuple[bytes, bytes], ...] = ()
    watch = False
    item_check = None
    stream_cls = stream_class(shape.cls)
    if shape.stream_item is not None and stream_cls is not None:
        frames = stream_cls.frames
        defaults = MutableHeaders()
        for name, value in stream_cls.default_headers:
            defaults.add(name, value)
        stream_headers = tuple(defaults.raw)
        watch = stream_cls.watch_disconnect
        if debug:
            item_check = _item_check(shape, codec, encode_json, operation)
    return _Plan(
        status=shape.status,
        media_type=media_type,
        has_body=shape.body_type is not None,
        json_body=json_body,
        content_type=(b"content-type", media_type.encode("latin-1")),
        header_fields=tuple(
            _HeaderField(
                header.attribute,
                MutableHeaders.validated_name(header.wire_name),
                header.required,
            )
            for header in shape.headers
        ),
        check=check,
        declared=getattr(shape.body_type, "__name__", repr(shape.body_type)),
        frames=frames,
        stream_headers=stream_headers,
        watch_disconnect=watch,
        item_check=item_check,
    )


def _item_check(
    shape: ResponseShape,
    codec: Codec,
    encode_json: Callable[[object], bytes],
    operation: Operation,
) -> Callable[[object], None]:
    decode = codec.build_decoder(shape.stream_item, json=True)
    declared = getattr(shape.stream_item, "__name__", repr(shape.stream_item))

    def check(item: object) -> None:
        try:
            decode(encode_json(item))
        except CodecError as exc:
            raise RuntimeError(
                f"{operation.contract.__qualname__}.{operation.name} streamed an "
                f"item whose value does not match the declared {declared}: {exc}"
            ) from None

    return check


def compile_responses(
    operation: Operation, codec: Codec, *, debug: bool = False
) -> ResponseEncoder:
    """The encoder for the response classes ``operation`` declares.

    Only declared classes (and their subclasses) are accepted: the type checkers
    keep handlers inside the union, and this is the backstop when they do not.
    With ``debug`` the body is also checked against its declared type, by
    decoding the JSON the client would receive.
    """
    encode_json = codec.build_encoder()
    plans = {
        shape.cls: _plan(shape, codec, encode_json, operation, debug=debug)
        for shape in operation.responses
    }

    def find(reply: Reply) -> _Plan:
        plan = plans.get(type(reply))
        if plan is not None:
            return plan
        for base in type(reply).__mro__[1:]:
            if base in plans:
                return plans[base]
        declared = ", ".join(sorted(cls.__name__ for cls in plans))
        raise RuntimeError(
            f"{operation.contract.__qualname__}.{operation.name} returned "
            f"{type(reply).__name__}, which the contract does not declare "
            f"(declared: {declared})"
        )

    def encode(reply: Reply) -> Response:
        plan = find(reply)
        if plan.frames is not None:
            return _stream_response(reply, plan, plan.frames, encode_json, operation)
        body: bytes | None = None
        if plan.has_body:
            headers = MutableHeaders([plan.content_type])
            body = _body_bytes(getattr(reply, "body"), plan, encode_json)  # noqa: B009
            if plan.check is not None:
                _verify(plan.check, plan, body, reply, operation)
        else:
            headers = MutableHeaders()
        for attribute, name, required in plan.header_fields:
            value = getattr(reply, attribute)
            if value is not None:
                headers.add_known(name, value if isinstance(value, str) else str(value))
            elif required:
                # msgspec does not check types on construction, so None gets here.
                raise RuntimeError(
                    f"{operation.contract.__qualname__}.{operation.name} returned a "
                    f"{type(reply).__name__} without the required header field "
                    f"{attribute!r}"
                )
        return Response(plan.status, body, headers)

    return encode


def _stream_response(
    reply: Reply,
    plan: _Plan,
    frames: Callable[..., AsyncIterator[bytes]],
    encode_json: Callable[[object], bytes],
    operation: Operation,
) -> Response:
    headers = MutableHeaders([plan.content_type, *plan.stream_headers])
    for attribute, name, required in plan.header_fields:
        value = getattr(reply, attribute)
        if value is not None:
            headers.add_known(name, value if isinstance(value, str) else str(value))
        elif required:
            raise RuntimeError(
                f"{operation.contract.__qualname__}.{operation.name} returned a "
                f"{type(reply).__name__} without the required header field "
                f"{attribute!r}"
            )
    body = frames(reply, encode_json, check=plan.item_check)
    return Response(plan.status, body, headers, watch_disconnect=plan.watch_disconnect)


def _body_bytes(
    body: object, plan: _Plan, encode_json: Callable[[object], bytes]
) -> bytes:
    if plan.json_body:
        return encode_json(body)
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode()
    raise TypeError(
        f"a response with media type {plan.media_type} needs a str or bytes body, "
        f"got {type(body).__name__}"
    )


def _verify(
    check: Callable[[bytes], Any],
    plan: _Plan,
    body: bytes,
    reply: Reply,
    operation: Operation,
) -> None:
    try:
        check(body)
    except CodecError as exc:
        raise RuntimeError(
            f"{operation.contract.__qualname__}.{operation.name} returned a "
            f"{type(reply).__name__} whose body does not match the declared "
            f"{plan.declared}: {exc}"
        ) from None
