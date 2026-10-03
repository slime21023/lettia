import json
import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lettia import JSONValue
from lettia.errors import HTTPException
from tests.support.asgi import (
    http_context,
)
from tests.support.strategies import JSON_VALUES


@pytest.mark.contract("REQ-JSON")
@pytest.mark.parametrize("container", ["array", "object"])
async def test_json_depth_overflow_is_a_client_error(container: str) -> None:
    depth = sys.getrecursionlimit() + 100
    prefix, suffix = (b"[", b"]") if container == "array" else (b'{"a":', b"}")
    payload = prefix * depth + b"0" + suffix * depth
    ctx = http_context(method="POST", body=payload)
    with pytest.raises(HTTPException, match="nesting depth") as error:
        await ctx.json()
    assert error.value.status_code == 400
    assert await ctx.body() == payload


@pytest.mark.contract("REQ-JSON")
async def test_decoded_json_validation_depth_overflow_is_a_client_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nested: JSONValue = 0
    for _ in range(sys.getrecursionlimit() + 100):
        nested = [nested]

    def decoded_json(text: str) -> JSONValue:
        return nested

    monkeypatch.setattr("lettia.context.json.loads", decoded_json)
    with pytest.raises(HTTPException, match="nesting depth") as error:
        await http_context(body=b"[]").json()
    assert error.value.status_code == 400


@pytest.mark.contract("REQ-JSON")
async def test_json_integer_decoder_limit_is_a_client_error() -> None:
    limit = sys.get_int_max_str_digits()
    if limit == 0:
        pytest.skip("Python integer string limit is disabled")
    ctx = http_context(body=b'{"number":' + b"9" * (limit + 1) + b"}")

    with pytest.raises(HTTPException, match="Invalid JSON") as error:
        await ctx.json()

    assert error.value.status_code == 400


@pytest.mark.contract("REQ-JSON")
@given(value=JSON_VALUES)
async def test_json_request_round_trip(value: JSONValue) -> None:
    payload = json.dumps(value, allow_nan=False).encode()
    ctx = http_context(method="POST", body=payload)
    assert await ctx.json() == value
    assert await ctx.text() == payload.decode()
    assert await ctx.body() == payload


@pytest.mark.contract("REQ-JSON")
@given(payload=st.sampled_from([b"{", b"\xff", b'"unterminated']))
async def test_invalid_json_is_a_client_error(payload: bytes) -> None:
    with pytest.raises(HTTPException) as error:
        await http_context(body=payload).json()
    assert error.value.status_code == 400


@pytest.mark.contract("REQ-JSON")
@pytest.mark.asyncio
async def test_invalid_json_is_reported_as_bad_request() -> None:
    with pytest.raises(HTTPException, match="Invalid JSON") as exc_info:
        await http_context(body=b"{bad").json()
    assert exc_info.value.status_code == 400


@pytest.mark.contract("JSON-VALUE")
def test_json_validation_compatibility_entry_matches_pure_rule() -> None:
    from lettia._json import validate_json_value
    from lettia.context import validate_json_value as compatibility

    assert compatibility is validate_json_value
    assert validate_json_value({"nested": [None, 2, "ok"]}) == {
        "nested": [None, 2, "ok"]
    }
    with pytest.raises(TypeError):
        validate_json_value({1: "invalid key"})


@pytest.mark.contract("JSON-VALUE")
@given(value=JSON_VALUES)
def test_json_value_rule_preserves_supported_nested_values(value: JSONValue) -> None:
    from lettia._json import validate_json_value

    validated = validate_json_value(value)
    assert validated == value
    if isinstance(value, (list, dict)):
        assert validated is not value


@pytest.mark.contract("JSON-VALUE")
@pytest.mark.parametrize("value", [b"bytes", (1, 2), {"nested": {1: "invalid"}}])
def test_json_value_rule_rejects_unsupported_values(value: object) -> None:
    from lettia._json import validate_json_value

    with pytest.raises(TypeError):
        validate_json_value(value)
