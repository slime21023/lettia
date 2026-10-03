import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lettia import JSONValue
from lettia.asgi import HTTPSendEvent
from lettia.response import (
    JsonResponse,
    ResponseWriter,
    TextResponse,
    normalize_response,
)
from tests.support.asgi import http_sender, response_body
from tests.support.strategies import JSON_VALUES, TEXT


@pytest.mark.contract("RESP-VALUES")
def test_response_types() -> None:
    text_resp = TextResponse("Hello World", status_code=201)
    assert text_resp.status_code == 201
    assert text_resp.body == b"Hello World"
    assert text_resp.media_type == "text/plain; charset=utf-8"

    json_resp = JsonResponse({"msg": "ok"}, status_code=200)
    assert json_resp.body == b'{"msg": "ok"}'
    assert json_resp.media_type == "application/json"


@pytest.mark.contract("RESP-VALUES")
def test_normalize_response() -> None:
    r1 = normalize_response("hello")
    assert isinstance(r1, TextResponse)
    assert r1.body == b"hello"

    r2 = normalize_response({"a": 1})
    assert isinstance(r2, JsonResponse)
    assert r2.body == b'{"a": 1}'

    r3 = normalize_response(("created", 201, {"x-test": "1"}))
    assert r3.status_code == 201
    assert r3.headers["x-test"] == "1"

    bytes_response = normalize_response(b"payload")
    list_response = normalize_response(["one", 2])
    tuple_response = normalize_response(("accepted", 202))
    assert bytes_response.media_type == "application/octet-stream"
    assert isinstance(list_response, JsonResponse)
    assert tuple_response.status_code == 202


@pytest.mark.contract("RESP-VALUES")
@given(value=JSON_VALUES)
async def test_json_response_round_trip(value: JSONValue) -> None:
    sent: list[HTTPSendEvent] = []
    await ResponseWriter(http_sender(sent)).write(JsonResponse(value))
    assert json.loads(response_body(sent)) == value


@pytest.mark.contract("RESP-VALUES")
@given(value=TEXT, status=st.integers(200, 599))
def test_response_normalization_preserves_text_and_metadata(
    value: str, status: int
) -> None:
    response = normalize_response((value, status, {"X-Test": "value"}))
    assert response.body.decode() == value and response.status_code == status
    assert response.headers["x-test"] == "value"
    assert normalize_response(response) is response
