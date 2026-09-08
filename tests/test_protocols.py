import dataclasses

import pytest
from asgi_helpers import http_context
from attrs import define
from pydantic import BaseModel

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


class UserModel(BaseModel):
    name: str
    age: int = 18


@dataclasses.dataclass
class CoercedPayload:
    enabled: bool
    ratio: float
    optional_count: int | None = None


@pytest.mark.asyncio
async def test_attrs_binder() -> None:
    user = await AttrsBinder().bind(
        http_context(method="POST", query_string=b"age=25", body=b'{"name": "Alice"}'),
        UserAttrs,
    )
    assert user.name == "Alice"
    assert user.age == 25


@pytest.mark.asyncio
async def test_attrs_binder_rejects_invalid_integer() -> None:
    with pytest.raises(HTTPException, match="Expected an integer") as exc_info:
        await AttrsBinder().bind(
            http_context(
                method="POST", body=b'{"name": "Alice", "age": "not-a-number"}'
            ),
            UserAttrs,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_dataclass_binder() -> None:
    user = await DataclassBinder().bind(
        http_context(
            method="POST", query_string=b"role=admin", body=b'{"name": "Bob"}'
        ),
        UserDC,
    )
    assert user.name == "Bob"
    assert user.role == "admin"


@pytest.mark.asyncio
async def test_pydantic_binder() -> None:
    user = await PydanticBinder().bind(
        http_context(method="POST", query_string=b"age=25", body=b'{"name": "Carol"}'),
        UserModel,
    )
    assert user.name == "Carol"
    assert user.age == 25


@pytest.mark.asyncio
async def test_binders_coerce_boolean_float_and_optional_values() -> None:
    payload = await DataclassBinder().bind(
        http_context(
            method="POST",
            query_string=b"enabled=yes&ratio=1.5&optional_count=7",
        ),
        CoercedPayload,
    )
    assert payload.enabled is True
    assert payload.ratio == 1.5
    assert payload.optional_count == 7


@pytest.mark.asyncio
async def test_binders_reject_wrong_target_and_invalid_boolean() -> None:
    with pytest.raises(TypeError, match="attrs class"):
        await AttrsBinder().bind(http_context(), UserDC)
    with pytest.raises(TypeError, match="dataclass"):
        await DataclassBinder().bind(http_context(), UserAttrs)
    with pytest.raises(HTTPException, match="Expected a boolean") as exc_info:
        await DataclassBinder().bind(
            http_context(method="POST", query_string=b"enabled=perhaps&ratio=1.0"),
            CoercedPayload,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pydantic_binder_reports_validation_errors() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await PydanticBinder().bind(
            http_context(method="POST", body=b'{"name": 1}'), UserModel
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_context_bind_selects_supported_binders() -> None:
    attrs_user = await http_context(method="POST", body=b'{"name": "Attrs"}').bind(
        UserAttrs
    )
    dataclass_user = await http_context(
        method="POST", body=b'{"name": "Dataclass"}'
    ).bind(UserDC)
    pydantic_user = await http_context(
        method="POST", body=b'{"name": "Pydantic"}'
    ).bind(UserModel)

    assert attrs_user.name == "Attrs"
    assert dataclass_user.name == "Dataclass"
    assert pydantic_user.name == "Pydantic"


def test_validator() -> None:
    def valid_adult(payload: dict[str, int]) -> bool:
        return payload.get("age", 0) >= 18

    validator = CallableValidator[dict[str, int]](valid_adult)
    validator.validate({"age": 20})
    with pytest.raises(HTTPException) as exc_info:
        validator.validate({"age": 16})
    assert exc_info.value.status_code == 400


def test_renderer() -> None:
    response = SimpleHTMLRenderer({"index": "<h1>Hello {{name}}!</h1>"}).render(
        "index", context={"name": "Lettia"}
    )
    assert response.status_code == 200
    assert response.media_type == "text/html; charset=utf-8"
    assert response.body == b"<h1>Hello Lettia!</h1>"
