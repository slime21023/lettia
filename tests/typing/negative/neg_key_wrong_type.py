from lettia import Key
from lettia.core import Context

RequestId = Key[int]("request_id")


def use(c: Context) -> None:
    RequestId.set(c, "abc")
