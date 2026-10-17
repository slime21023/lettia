from collections.abc import AsyncIterator

import pytest

from lettia.core import Context, Key, Request


async def no_body() -> AsyncIterator[bytes]:
    return
    yield b""  # pragma: no cover - makes this an async generator


def make_context() -> Context:
    return Context(Request(method="GET", path="/", body=no_body()))


def test_key_returns_the_value_that_was_set() -> None:
    key = Key[int]("count")
    c = make_context()

    key.set(c, 3)

    assert key.get(c) == 3


def test_key_get_before_set_raises_lookup_error_naming_the_key() -> None:
    with pytest.raises(LookupError, match="request_id"):
        Key[str]("request_id").get(make_context())


def test_key_get_with_default_returns_default_when_unset() -> None:
    key = Key[str]("request_id")
    c = make_context()

    assert key.get(c, None) is None
    assert key.get(c, "fallback") == "fallback"


def test_key_get_with_default_returns_stored_value_even_when_none() -> None:
    key = Key[str | None]("maybe")
    c = make_context()

    key.set(c, None)

    assert key.get(c, "fallback") is None


def test_key_set_again_replaces_the_value() -> None:
    key = Key[str]("name")
    c = make_context()

    key.set(c, "a")
    key.set(c, "b")

    assert key.get(c) == "b"


def test_key_values_belong_to_one_context() -> None:
    key = Key[str]("name")
    first, second = make_context(), make_context()

    key.set(first, "only here")

    with pytest.raises(LookupError):
        key.get(second)


def test_keys_with_the_same_name_do_not_collide() -> None:
    one, two = Key[str]("same"), Key[str]("same")
    c = make_context()

    one.set(c, "1")
    two.set(c, "2")

    assert (one.get(c), two.get(c)) == ("1", "2")


def test_key_can_hold_falsy_values() -> None:
    key = Key[int | None]("maybe")
    c = make_context()

    key.set(c, None)

    assert key.get(c) is None


def test_key_works_on_context_subclasses() -> None:
    class Custom(Context):
        pass

    key = Key[str]("name")
    c = Custom(Request(method="GET", path="/", body=no_body()))

    key.set(c, "ok")

    assert key.get(c) == "ok"
