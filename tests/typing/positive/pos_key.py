"""``Key[T]`` fixes ``T`` through the instance."""

from typing import assert_type

from lettia import Key
from lettia.core import Context

RequestId = Key[str]("request_id")


def use(c: Context) -> None:
    RequestId.set(c, "abc")
    assert_type(RequestId.get(c), str)
    assert_type(RequestId.get(c, None), str | None)
    assert_type(RequestId.get(c, "fallback"), str)
