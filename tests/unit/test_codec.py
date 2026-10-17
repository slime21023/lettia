import msgspec
import pytest

from lettia.contract.codec import Codec, CodecError, MsgspecCodec


class NewUser(msgspec.Struct):
    name: str
    age: int = 0


class Params(msgspec.Struct, kw_only=True):
    user_id: int
    expand: bool = False
    tags: list[int] = []


codec: Codec = MsgspecCodec()


# --- strict JSON decoding -----------------------------------------------


def test_json_decoder_builds_the_typed_value() -> None:
    decode = codec.build_decoder(NewUser, json=True)

    assert decode(b'{"name": "Ann", "age": 3}') == NewUser("Ann", 3)


def test_json_decoder_is_strict_about_types() -> None:
    decode = codec.build_decoder(NewUser, json=True)

    with pytest.raises(CodecError) as caught:
        decode(b'{"name": "Ann", "age": "3"}')

    assert caught.value.path == "$.age"
    assert "int" in caught.value.message


def test_json_decoder_reports_a_missing_field_at_the_fields_path() -> None:
    with pytest.raises(CodecError) as caught:
        codec.build_decoder(NewUser, json=True)(b"{}")

    assert caught.value.path == "$.name"
    assert "missing" in caught.value.message


@pytest.mark.parametrize(
    ("data", "path"),
    [(b"[]", "$"), (b"", "$"), (b"{bad", "$"), (b'{"name": 1}', "$.name")],
)
def test_json_decoder_reports_malformed_and_mismatched_input(
    data: bytes, path: str
) -> None:
    with pytest.raises(CodecError) as caught:
        codec.build_decoder(NewUser, json=True)(data)

    assert caught.value.path == path


def test_json_decoder_reports_list_indexes_in_the_path() -> None:
    decode = codec.build_decoder(list[NewUser], json=True)

    with pytest.raises(CodecError) as caught:
        decode(b'[{"name": "a"}, {"name": 2}]')

    assert caught.value.path == "$[1].name"


# --- lax conversion of already parsed values ----------------------------


def test_lax_decoder_converts_strings_to_the_declared_types() -> None:
    decode = codec.build_decoder(Params, json=False)

    assert decode({"user_id": "42", "expand": "true", "tags": ["1", "2"]}) == Params(
        user_id=42, expand=True, tags=[1, 2]
    )


def test_lax_decoder_applies_defaults_for_absent_values() -> None:
    assert codec.build_decoder(Params, json=False)({"user_id": "1"}) == Params(
        user_id=1
    )


def test_lax_decoder_reports_a_bad_value_at_its_path() -> None:
    with pytest.raises(CodecError) as caught:
        codec.build_decoder(Params, json=False)({"user_id": "x"})

    assert caught.value.path == "$.user_id"


def test_lax_decoder_reports_the_element_of_a_bad_list() -> None:
    with pytest.raises(CodecError) as caught:
        codec.build_decoder(Params, json=False)({"user_id": "1", "tags": ["a"]})

    assert caught.value.path == "$.tags[0]"


def test_lax_decoder_reports_a_missing_field_at_the_fields_path() -> None:
    with pytest.raises(CodecError) as caught:
        codec.build_decoder(Params, json=False)({})

    assert caught.value.path == "$.user_id"


# --- encoding and schema ------------------------------------------------


def test_encoder_produces_compact_json_bytes() -> None:
    encode = codec.build_encoder()

    assert encode(NewUser("Ann", 3)) == b'{"name":"Ann","age":3}'
    assert encode([1, 2]) == b"[1,2]"
    assert encode(None) == b"null"


def test_json_schema_returns_one_schema_per_type_and_shared_components() -> None:
    schemas, components = codec.json_schema(
        [NewUser, list[NewUser]], ref_template="#/components/schemas/{name}"
    )

    assert schemas[0] == {"$ref": "#/components/schemas/NewUser"}
    assert schemas[1] == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/NewUser"},
    }
    assert components["NewUser"]["required"] == ["name"]


def test_json_schema_reports_two_types_with_the_same_name_as_a_codec_error() -> None:
    first = msgspec.defstruct("Clash", [("id", int)])
    second = msgspec.defstruct("Clash", [("id", str)])

    with pytest.raises(CodecError, match="Clash"):
        codec.json_schema([first, second], ref_template="#/components/schemas/{name}")


def test_codec_error_str_includes_path_and_message() -> None:
    error = CodecError("Expected `int`", "$.age")

    assert str(error) == "$.age: Expected `int`"
