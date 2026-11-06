import pytest

from lettia.core.cookies import format_set_cookie, parse_cookies


def test_cookies_parse_semicolon_separated_pairs() -> None:
    assert parse_cookies(["a=1; b=2"]) == {"a": "1", "b": "2"}


def test_cookies_merge_multiple_cookie_headers() -> None:
    assert parse_cookies(["a=1", "b=2"]) == {"a": "1", "b": "2"}


def test_cookies_first_value_of_a_repeated_name_wins() -> None:
    assert parse_cookies(["a=1; a=2"]) == {"a": "1"}


def test_cookies_value_keeps_inner_equals_signs() -> None:
    assert parse_cookies(["token=abc=="]) == {"token": "abc=="}


def test_cookies_surrounding_quotes_are_removed() -> None:
    assert parse_cookies(['a="hello world"']) == {"a": "hello world"}


@pytest.mark.parametrize("header", ["", " ; ", "novalue", "=x", "; ;"])
def test_cookies_malformed_pieces_are_ignored(header: str) -> None:
    assert parse_cookies([header]) == {}


def test_cookies_valid_pieces_survive_next_to_malformed_ones() -> None:
    assert parse_cookies(["bad; a=1; =x; b=2"]) == {"a": "1", "b": "2"}


def test_cookies_no_headers_gives_empty_mapping() -> None:
    assert parse_cookies([]) == {}


def test_set_cookie_defaults_are_http_only_and_same_site_lax() -> None:
    assert format_set_cookie("sid", "abc") == "sid=abc; Path=/; HttpOnly; SameSite=Lax"


def test_set_cookie_writes_every_attribute_in_a_fixed_order() -> None:
    header = format_set_cookie(
        "sid",
        "abc",
        max_age=60,
        expires=1_893_456_000,
        path="/app",
        domain="example.com",
        secure=True,
        httponly=False,
        samesite="strict",
    )

    assert header == (
        "sid=abc; Expires=Tue, 01 Jan 2030 00:00:00 GMT; Max-Age=60; "
        "Domain=example.com; Path=/app; Secure; SameSite=Strict"
    )


def test_set_cookie_without_same_site_omits_the_attribute() -> None:
    assert "SameSite" not in format_set_cookie("sid", "abc", samesite=None)


def test_set_cookie_same_site_none_needs_secure() -> None:
    with pytest.raises(ValueError, match="secure"):
        format_set_cookie("sid", "abc", samesite="none")

    assert "SameSite=None" in format_set_cookie(
        "sid", "abc", samesite="none", secure=True
    )


@pytest.mark.parametrize(
    "name", ["", "a b", "a;b", "a=b", "a\r\nb", "Path", "secure", "max-age"]
)
def test_set_cookie_rejects_names_that_are_not_tokens_or_read_as_attributes(
    name: str,
) -> None:
    with pytest.raises(ValueError, match="cookie name"):
        format_set_cookie(name, "x")


@pytest.mark.parametrize(
    "value", ["a b", "a;b", "a,b", 'a"b', "a\\b", "a\r\nSet-Cookie: x=y", "é"]
)
def test_set_cookie_rejects_values_that_could_end_the_cookie_or_inject(
    value: str,
) -> None:
    with pytest.raises(ValueError, match="cookie value"):
        format_set_cookie("sid", value)


@pytest.mark.parametrize("path", ["", "x", "/a;b", "/a\r\nb", "/a\x7f"])
def test_set_cookie_rejects_a_path_that_is_not_plain_and_absolute(path: str) -> None:
    with pytest.raises(ValueError, match="path"):
        format_set_cookie("sid", "x", path=path)


@pytest.mark.parametrize("domain", ["", "-a.com", "a.com-", "a b.com", "a.com;x"])
def test_set_cookie_rejects_an_invalid_domain(domain: str) -> None:
    with pytest.raises(ValueError, match="domain"):
        format_set_cookie("sid", "x", domain=domain)


def test_set_cookie_prefixes_demand_their_attributes() -> None:
    with pytest.raises(ValueError, match="__Secure-"):
        format_set_cookie("__Secure-a", "x")
    with pytest.raises(ValueError, match="__Host-"):
        format_set_cookie("__Host-a", "x", secure=True, path="/app")
    with pytest.raises(ValueError, match="__Host-"):
        format_set_cookie("__Host-a", "x", secure=True, domain="example.com")

    assert format_set_cookie("__Host-a", "x", secure=True).startswith("__Host-a=x")


def test_set_cookie_refuses_what_browsers_would_silently_drop() -> None:
    with pytest.raises(ValueError, match="4096"):
        format_set_cookie("sid", "x" * 4096)


def test_set_cookie_value_round_trips_through_the_request_parser() -> None:
    header = format_set_cookie("sid", "a1-_.~=/+")
    sent = header.split(";", 1)[0]

    assert parse_cookies([sent]) == {"sid": "a1-_.~=/+"}
