import dataclasses
from typing import Any

import pytest
from attrs import define

from lettia.context import Context
from lettia.errors import HTTPException
from lettia.protocols import (
    AttrsBinder,
    CallableValidator,
    DataclassBinder,
    PydanticBinder,
    SimpleHTMLRenderer,
)


@define(slots=True)
class UserAttrs:
    name: str
    age: int = 18


@dataclasses.dataclass
class UserDC:
    name: str
    role: str = "guest"


@pytest.mark.asyncio
async def test_attrs_binder() -> None:
    scope = {"type": "http", "method": "POST", "query_string": b"age=25"}

    async def receive() -> dict[str, Any]:
        return {
            "type": "http.request",
            "body": b'{"name": "Alice"}',
            "more_body": False,
        }

    ctx = Context(scope=scope, receive=receive, send=None)
    binder = AttrsBinder()
    user = await binder.bind(ctx, UserAttrs)

    assert isinstance(user, UserAttrs)
    assert user.name == "Alice"
    assert user.age == 25


@pytest.mark.asyncio
async def test_attrs_binder_rejects_invalid_integer() -> None:
    scope = {"type": "http", "method": "POST"}

    async def receive() -> dict[str, Any]:
        return {
            "type": "http.request",
            "body": b'{"name": "Alice", "age": "not-a-number"}',
            "more_body": False,
        }

    ctx = Context(scope=scope, receive=receive, send=None)

    with pytest.raises(HTTPException, match="Expected an integer") as exc_info:
        await AttrsBinder().bind(ctx, UserAttrs)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_dataclass_binder() -> None:
    scope = {"type": "http", "method": "POST", "query_string": b"role=admin"}

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b'{"name": "Bob"}', "more_body": False}

    ctx = Context(scope=scope, receive=receive, send=None)
    binder = DataclassBinder()
    user = await binder.bind(ctx, UserDC)

    assert isinstance(user, UserDC)
    assert user.name == "Bob"
    assert user.role == "admin"


@pytest.mark.asyncio
async def test_pydantic_binder() -> None:
    pydantic = pytest.importorskip("pydantic")

    class UserModel(pydantic.BaseModel):
        name: str
        age: int = 18

    scope = {"type": "http", "method": "POST", "query_string": b"age=25"}

    async def receive() -> dict[str, Any]:
        return {
            "type": "http.request",
            "body": b'{"name": "Carol"}',
            "more_body": False,
        }

    ctx = Context(scope=scope, receive=receive, send=None)
    user = await PydanticBinder().bind(ctx, UserModel)

    assert isinstance(user, UserModel)
    assert user.name == "Carol"
    assert user.age == 25


def test_validator() -> None:
    validator = CallableValidator(lambda obj: obj.get("age", 0) >= 18)

    # Valid
    validator.validate({"age": 20})

    # Invalid
    with pytest.raises(HTTPException) as exc_info:
        validator.validate({"age": 16})
    assert exc_info.value.status_code == 400


def test_renderer() -> None:
    renderer = SimpleHTMLRenderer(templates={"index": "<h1>Hello {{name}}!</h1>"})
    resp = renderer.render("index", context={"name": "Lettia"})

    assert resp.status_code == 200
    assert resp.media_type == "text/html; charset=utf-8"
    assert resp.body == b"<h1>Hello Lettia!</h1>"
