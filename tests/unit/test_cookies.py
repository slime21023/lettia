import pytest

from lettia.core.cookies import parse_cookies


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
