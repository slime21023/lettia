# pyright: reportUnusedImport=false
from typing import Annotated, Protocol

import msgspec

from lettia import Context, GuardSpec, HeaderName, NoContent, NotFound, Ok, Created, op


class User(msgspec.Struct): ...


class ApiKey(msgspec.Struct): ...


class Admin(msgspec.Struct): ...


class Req(msgspec.Struct):
    x: int


bearer = GuardSpec[User]("bearer")


async def lookup(head: object) -> ApiKey:
    return ApiKey()


bearer.use(lookup)
