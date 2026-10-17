"""Compile an operation's response union into a function that builds a Response."""

from collections.abc import Callable
from dataclasses import dataclass

from lettia.contract.codec import Codec
from lettia.contract.introspect import Operation, ResponseShape
from lettia.contract.responses import Reply
from lettia.core import MutableHeaders, Response

type ResponseEncoder = Callable[[Reply], Response]


@dataclass(frozen=True, slots=True)
class _Plan:
    status: int
    media_type: str
    has_body: bool
    json_body: bool
    header_fields: tuple[tuple[str, str], ...]


def _plan(shape: ResponseShape) -> _Plan:
    media = shape.media_type.split(";", 1)[0].strip().lower()
    return _Plan(
        status=shape.status,
        media_type=shape.media_type,
        has_body=shape.body_type is not None,
        json_body=media == "application/json" or media.endswith("+json"),
        header_fields=shape.headers,
    )


def compile_responses(operation: Operation, codec: Codec) -> ResponseEncoder:
    """The encoder for the response classes ``operation`` declares.

    Only declared classes (and their subclasses) are accepted: the type checkers
    keep handlers inside the union, and this is the backstop when they do not.
    """
    plans = {shape.cls: _plan(shape) for shape in operation.responses}
    encode_json = codec.build_encoder()

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
        headers = MutableHeaders()
        body: bytes | None = None
        if plan.has_body:
            headers.set("content-type", plan.media_type)
            body = _body_bytes(getattr(reply, "body"), plan, encode_json)  # noqa: B009
        for attribute, wire_name in plan.header_fields:
            value = getattr(reply, attribute)
            if value is not None:
                headers.set(wire_name, value if isinstance(value, str) else str(value))
        return Response(plan.status, body, headers)

    return encode


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
