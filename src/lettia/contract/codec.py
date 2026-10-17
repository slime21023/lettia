"""The codec seam: how values are decoded, encoded and described as JSON Schema.

0.1 implements only msgspec. The protocol has three operations so that another
model library could be added later without touching the rest of the contract
layer.
"""

import re
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import msgspec


class CodecError(Exception):
    """A value was rejected; ``path`` locates it, for example ``$.items[0].name``."""

    def __init__(self, message: str, path: str) -> None:
        super().__init__(f"{path}: {message}")
        self.message = message
        self.path = path


class Codec(Protocol):
    def build_decoder(self, type_: Any, *, json: bool) -> Callable[[Any], Any]:
        """A function that decodes into ``type_`` and raises :class:`CodecError`.

        With ``json=True`` it takes JSON bytes and is strict about types. With
        ``json=False`` it takes already parsed values, such as the strings of a
        query string, and converts them (``"42"`` to ``42``).
        """
        ...

    def build_encoder(self) -> Callable[[object], bytes]:
        """A function that encodes a value as JSON bytes."""
        ...

    def json_schema(
        self, types: Sequence[Any], *, ref_template: str
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """One schema (usually a ``$ref``) per type, and the shared components.

        ``ref_template`` formats a component name into its ``$ref``; raises
        :class:`CodecError` when two different types would share a name.
        """
        ...


_AT_PATH = re.compile(r" - at `(\$[^`]*)`$")
_MISSING_FIELD = re.compile(r"missing required field `([^`]+)`")


def _to_codec_error(exc: msgspec.DecodeError) -> CodecError:
    message, path = str(exc), "$"
    if located := _AT_PATH.search(message):
        path, message = located.group(1), message[: located.start()]
    if missing := _MISSING_FIELD.search(message):
        # msgspec reports a missing field at its parent; point at the field itself.
        path = f"{path}.{missing.group(1)}"
    return CodecError(message, path)


class MsgspecCodec:
    def build_decoder(self, type_: Any, *, json: bool) -> Callable[[Any], Any]:
        if json:
            json_decoder = msgspec.json.Decoder(type_)

            def decode_json(data: Any) -> Any:
                try:
                    return json_decoder.decode(data)
                except msgspec.DecodeError as exc:
                    raise _to_codec_error(exc) from None

            return decode_json

        def convert(raw: Any) -> Any:
            try:
                return msgspec.convert(raw, type_, strict=False)
            except msgspec.ValidationError as exc:
                raise _to_codec_error(exc) from None

        return convert

    def build_encoder(self) -> Callable[[object], bytes]:
        return msgspec.json.Encoder().encode

    def json_schema(
        self, types: Sequence[Any], *, ref_template: str
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        try:
            schemas, components = msgspec.json.schema_components(
                types, ref_template=ref_template
            )
        except KeyError as exc:
            clashing = exc.args[0] if exc.args else None
            name = getattr(clashing, "__name__", repr(clashing))
            raise CodecError(
                f"another schema is also named {name!r}; rename one of the types",
                "$",
            ) from None
        return list(schemas), components
