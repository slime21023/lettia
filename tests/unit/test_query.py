import pytest

from lettia.core import QueryParams


def test_query_parse_reads_simple_pairs() -> None:
    query = QueryParams.parse(b"a=1&b=2")

    assert dict(query) == {"a": "1", "b": "2"}


def test_query_repeated_name_is_multi_valued() -> None:
    query = QueryParams.parse(b"tag=x&tag=y&other=1")

    assert query["tag"] == "x"
    assert query.get_all("tag") == ["x", "y"]
    assert list(query) == ["tag", "other"]
    assert len(query) == 2


def test_query_percent_and_plus_are_decoded() -> None:
    query = QueryParams.parse(b"name=caf%C3%A9&q=a+b&sym=%26%3D")

    assert (query["name"], query["q"], query["sym"]) == ("café", "a b", "&=")


def test_query_blank_values_are_kept() -> None:
    query = QueryParams.parse(b"a=&b")

    assert (query["a"], query["b"]) == ("", "")


def test_query_empty_string_has_no_entries() -> None:
    query = QueryParams.parse(b"")

    assert len(query) == 0
    assert query.get("a") is None
    assert query.get_all("a") == []


def test_query_missing_name_raises_key_error() -> None:
    with pytest.raises(KeyError):
        QueryParams.parse(b"a=1")["b"]


def test_query_invalid_utf8_is_replaced_instead_of_raising() -> None:
    assert QueryParams.parse(b"a=%FF")["a"] == "�"


def test_query_names_are_case_sensitive() -> None:
    assert "A" not in QueryParams.parse(b"a=1")
