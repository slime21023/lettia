import pytest

from lettia.core import Headers, MutableHeaders

RAW = [
    (b"host", b"example.com"),
    (b"accept", b"text/html"),
    (b"accept", b"application/json"),
]


def test_headers_get_is_case_insensitive() -> None:
    assert Headers(RAW).get("Host") == "example.com"


def test_headers_get_returns_first_value_of_a_repeated_name() -> None:
    assert Headers(RAW)["accept"] == "text/html"


def test_headers_get_all_preserves_order() -> None:
    assert Headers(RAW).get_all("Accept") == ["text/html", "application/json"]


def test_headers_iterate_unique_names_in_first_seen_order() -> None:
    headers = Headers(RAW)

    assert list(headers) == ["host", "accept"]
    assert len(headers) == 2


def test_headers_items_yield_first_value_per_name() -> None:
    assert dict(Headers(RAW).items()) == {
        "host": "example.com",
        "accept": "text/html",
    }


def test_headers_multi_items_keep_every_pair() -> None:
    assert Headers(RAW).multi_items() == [
        ("host", "example.com"),
        ("accept", "text/html"),
        ("accept", "application/json"),
    ]


def test_headers_contains_is_case_insensitive() -> None:
    assert "HOST" in Headers(RAW)
    assert "missing" not in Headers(RAW)


def test_headers_empty_has_no_entries() -> None:
    headers = Headers([])

    assert len(headers) == 0
    assert headers.get("host") is None
    assert headers.get_all("host") == []


def test_headers_get_missing_returns_the_default() -> None:
    assert Headers(RAW).get("x-missing", "fallback") == "fallback"


def test_headers_getitem_missing_raises_key_error() -> None:
    with pytest.raises(KeyError):
        Headers(RAW)["x-missing"]


def test_headers_values_are_decoded_as_latin_1() -> None:
    assert Headers([(b"x-name", b"caf\xe9")])["x-name"] == "café"


def test_headers_from_pairs_lowercases_names() -> None:
    headers = Headers.from_pairs([("Content-Type", "text/plain")])

    assert headers.multi_items() == [("content-type", "text/plain")]


def test_mutable_headers_set_replaces_every_existing_value() -> None:
    headers = MutableHeaders(list(RAW))

    headers.set("Accept", "text/plain")

    assert headers.get_all("accept") == ["text/plain"]
    assert headers["host"] == "example.com"


def test_mutable_headers_add_appends_without_replacing() -> None:
    headers = MutableHeaders()

    headers.add("Set-Cookie", "a=1")
    headers.add("set-cookie", "b=2")

    assert headers.get_all("set-cookie") == ["a=1", "b=2"]


def test_mutable_headers_remove_deletes_every_value() -> None:
    headers = MutableHeaders(list(RAW))

    headers.remove("ACCEPT")

    assert "accept" not in headers
    assert "host" in headers


def test_mutable_headers_remove_missing_name_is_a_no_op() -> None:
    headers = MutableHeaders()

    headers.remove("x-missing")

    assert len(headers) == 0


def test_mutable_headers_raw_exposes_lowercase_bytes_for_the_adapter() -> None:
    headers = MutableHeaders()

    headers.set("Content-Type", "text/plain")

    assert headers.raw == [(b"content-type", b"text/plain")]


@pytest.mark.parametrize("value", ["a\r\nx-evil: 1", "a\nb", "a\rb"])
def test_mutable_headers_reject_line_breaks_in_values(value: str) -> None:
    with pytest.raises(ValueError, match="line break"):
        MutableHeaders().set("x-a", value)


@pytest.mark.parametrize("name", ["", "bad name", "bad:name", "x\r\ny"])
def test_mutable_headers_reject_invalid_names(name: str) -> None:
    with pytest.raises(ValueError, match="header name"):
        MutableHeaders().add(name, "v")


def test_mutable_headers_reject_values_outside_latin_1() -> None:
    with pytest.raises(ValueError):
        MutableHeaders().set("x-a", "日本語")
