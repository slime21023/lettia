"""Guard compositions infer the principal type."""

from typing import assert_type

import msgspec

from lettia import ApiKeyHeader, App, GuardSpec, RequestHead, public


class User(msgspec.Struct): ...


class ApiKey(msgspec.Struct): ...


class Admin(msgspec.Struct): ...


bearer = GuardSpec[User]("bearer")
apikey = GuardSpec[ApiKey]("apikey", scheme=ApiKeyHeader("X-API-Key"))
admin = GuardSpec[Admin]("admin")

assert_type(bearer | apikey, GuardSpec[User | ApiKey])
assert_type(bearer & admin, GuardSpec[tuple[User, Admin]])
assert_type(public, GuardSpec[None])


async def verify(head: RequestHead) -> User:
    return User()


impl = bearer.use(verify)


optional = bearer | public
assert_type(optional, GuardSpec[User | None])

app = App(guards=[impl])
