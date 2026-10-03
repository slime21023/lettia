from http.cookies import SimpleCookie

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lettia.asgi import HTTPSendEvent
from lettia.response import (
    Response,
    ResponseWriter,
)
from tests.support.asgi import http_context, http_sender


@pytest.mark.contract("RESP-COOKIES")
@pytest.mark.parametrize(
    "name", ["token", "__Host-token", "__Secure-token", "__HOST-token"]
)
@pytest.mark.parametrize("samesite", ["lax", "strict", "none"])
def test_delete_cookie_preserves_explicit_security_attributes(
    name: str, samesite: str
) -> None:
    response = Response()
    response.delete_cookie(name, secure=True, httponly=True, samesite=samesite)
    cookies = SimpleCookie(response.headers["set-cookie"])
    cookie = cookies[name]
    assert cookie.value == "" and cookie["max-age"] == "0"
    assert cookie["secure"] and cookie["httponly"]
    assert cookie["samesite"] == samesite and cookie["path"] == "/"


@pytest.mark.contract("RESP-COOKIES")
@pytest.mark.parametrize("prefix", ["__Host-", "__Secure-", "__HOST-"])
def test_delete_prefixed_cookie_is_secure_without_extra_arguments(prefix: str) -> None:
    response = Response()
    response.delete_cookie(prefix + "token")
    assert "Secure" in response.headers["set-cookie"]


@pytest.mark.contract("RESP-COOKIES")
def test_delete_cookie_keeps_positional_scope_and_validates_atomically() -> None:
    response = Response()
    response.delete_cookie("token", "/account", "example.com")
    before = response.headers.copy()
    assert "Path=/account" in before["set-cookie"]
    assert "Domain=example.com" in before["set-cookie"]
    with pytest.raises(ValueError):
        response.delete_cookie("token", secure=True, samesite="none; Secure")
    assert response.headers == before


@pytest.mark.contract("RESP-COOKIES")
@pytest.mark.parametrize("delete", [False, True])
@pytest.mark.parametrize("domain", [None, "example.com"])
def test_cookie_prefix_scope_rules_apply_to_creation_and_deletion(
    delete: bool, domain: str | None
) -> None:
    response = Response(headers={"set-cookie": "existing=1"})
    path = "/" if domain else "/account"
    with pytest.raises(ValueError, match="__Host-"):
        if delete:
            response.delete_cookie("__Host-token", path, domain)
        else:
            response.set_cookie(
                "__Host-token", "value", path=path, domain=domain, secure=True
            )
    assert response.headers == {"set-cookie": "existing=1"}


@pytest.mark.contract("RESP-COOKIES")
def test_cookie_creation_requires_secure_for_security_prefix() -> None:
    with pytest.raises(ValueError, match="require Secure"):
        Response().set_cookie("__Secure-token", "value")


@pytest.mark.contract("RESP-COOKIES")
def test_response_headers_and_cookies() -> None:
    resp = Response(status_code=200)
    resp.set_header("X-Custom-Header", "test-val")
    resp.set_cookie("session", "abc", max_age=3600, httponly=True)

    assert resp.headers["x-custom-header"] == "test-val"
    assert "session=abc" in resp.headers["set-cookie"]
    assert "HttpOnly" in resp.headers["set-cookie"]


@pytest.mark.contract("RESP-COOKIES")
def test_response_delete_cookie_and_header_validation() -> None:
    resp = Response()
    resp.delete_cookie("session")

    assert "session=; Path=/; Max-Age=0" in resp.headers["set-cookie"]

    with pytest.raises(ValueError, match="cannot contain newlines"):
        resp.set_header("x-test", "safe\r\nInjected: true")
    with pytest.raises(ValueError, match="cannot contain newlines"):
        resp.set_cookie("session", "safe\nInjected")


@pytest.mark.contract("RESP-COOKIES")
def test_response_cookie_optional_attributes_and_validation() -> None:
    response = Response()
    response.set_cookie(
        "token",
        "value",
        domain="example.com",
        expires="Wed, 21 Oct 2030 07:28:00 GMT",
        secure=True,
        samesite="strict",
    )
    cookie = response.headers["set-cookie"]
    assert "Domain=example.com" in cookie
    assert "Expires=Wed, 21 Oct 2030 07:28:00 GMT" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie

    with pytest.raises(ValueError, match="cannot contain newlines"):
        response.set_cookie("token", "value", domain="bad\nexample.com")
    with pytest.raises(ValueError, match="cannot contain newlines"):
        response.set_cookie("token", "value", samesite="strict\ninvalid")


@pytest.mark.contract("RESP-COOKIES")
@pytest.mark.asyncio
async def test_response_writer_validates_content_length_and_multiple_cookies() -> None:
    sent_messages: list[HTTPSendEvent] = []
    writer = ResponseWriter(send=http_sender(sent_messages))
    invalid_response = Response(headers={"content-length": "99"}, body=b"OK")
    with pytest.raises(ValueError, match="does not match"):
        await writer.write(invalid_response)

    response = Response(body=b"OK")
    response.set_cookie("first", "one")
    response.set_cookie("second", "two")
    await writer.write(response)

    assert sent_messages[0]["type"] == "http.response.start"
    headers = sent_messages[0]["headers"]
    assert headers.count((b"content-length", b"2")) == 1
    assert (b"set-cookie", b"first=one; Path=/; SameSite=lax") in headers
    assert (b"set-cookie", b"second=two; Path=/; SameSite=lax") in headers


@pytest.mark.contract("RESP-COOKIES")
@pytest.mark.parametrize(
    "attribute", ["key", "value", "path", "domain", "samesite", "expires"]
)
@pytest.mark.parametrize("newline", ["\r", "\n", "\r\n"])
def test_cookie_invalid_attribute_leaves_headers_unchanged(
    attribute: str,
    newline: str,
) -> None:
    response = Response(headers={"Set-Cookie": "first=1", "set-cookie": "second=2"})
    before = response.headers.copy()
    attributes = dict.fromkeys(
        ["key", "value", "path", "domain", "samesite", "expires"], "safe"
    )
    attributes[attribute] = f"safe{newline}injected=1"

    with pytest.raises(ValueError, match="cannot contain newlines"):
        response.set_cookie(
            attributes["key"],
            attributes["value"],
            path=attributes["path"],
            domain=attributes["domain"],
            samesite=attributes["samesite"],
            expires=attributes["expires"],
        )

    assert response.headers == before


@pytest.mark.contract("RESP-COOKIES")
async def test_cookie_append_preserves_all_case_variants_and_order() -> None:
    response = Response(headers={"Set-Cookie": "first=1", "SET-cookie": "second=2"})
    response.set_cookie("third", "3")
    response.delete_cookie("fourth")
    sent: list[HTTPSendEvent] = []

    await ResponseWriter(http_sender(sent)).write(response)

    start = sent[0]
    assert start["type"] == "http.response.start"
    cookies = [value for key, value in start["headers"] if key == b"set-cookie"]
    assert cookies[:2] == [b"first=1", b"second=2"]
    assert cookies[2].startswith(b"third=3;")
    assert cookies[3].startswith(b"fourth=; Path=/; Max-Age=0;")


@pytest.mark.contract("RESP-COOKIES")
@pytest.mark.parametrize(
    "attribute", ["key", "value", "path", "domain", "samesite", "expires"]
)
@given(separator=st.sampled_from(["; Domain=example.com", "\t", "\x7f", "é"]))
def test_cookie_structure_validation_is_atomic(attribute: str, separator: str) -> None:
    attributes = {
        "key": "name",
        "value": "value",
        "path": "/",
        "domain": "example.com",
        "samesite": "lax",
        "expires": "Wed, 21 Oct 2030 07:28:00 GMT",
    }
    attributes[attribute] += separator
    response = Response(headers={"Set-Cookie": "existing=1"})
    with pytest.raises(ValueError):
        response.set_cookie(
            attributes["key"],
            attributes["value"],
            path=attributes["path"],
            domain=attributes["domain"],
            samesite=attributes["samesite"],
            expires=attributes["expires"],
        )
    assert response.headers == {"Set-Cookie": "existing=1"}


@pytest.mark.contract("RESP-COOKIES")
@given(
    value=st.text(
        alphabet=st.characters(
            min_codepoint=33, max_codepoint=126, exclude_characters='";\\,'
        )
    )
)
def test_cookie_legal_values_round_trip_without_extra_attributes(value: str) -> None:
    response = Response()
    response.set_cookie("name", value)
    cookie_pair, attributes = response.headers["set-cookie"].split(";", 1)
    ctx = http_context(headers=[(b"cookie", cookie_pair.encode("ascii"))])
    assert ctx.cookie("name") == value
    assert attributes == " Path=/; SameSite=lax"
