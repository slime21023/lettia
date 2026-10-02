from __future__ import annotations

import dataclasses
import json
from html.parser import HTMLParser
from typing import override

import pytest
from asgi_helpers import http_context
from attrs import define
from hypothesis import example, given
from hypothesis import strategies as st
from pydantic import BaseModel
from strategies import TEXT

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


@dataclasses.dataclass
class NullableDC:
    name: str | None
    count: int | None
    ratio: float | None
    active: bool | None


@define(slots=True)
class NullableAttrs:
    name: str | None
    count: int | None
    ratio: float | None
    active: bool | None


@given(
    name=st.one_of(st.none(), TEXT),
    count=st.one_of(st.none(), st.integers(-(2**63), 2**63 - 1)),
    ratio=st.one_of(st.none(), st.floats(allow_nan=False, allow_infinity=False)),
    active=st.one_of(st.none(), st.booleans()),
    use_attrs=st.booleans(),
)
@example(name=None, count=None, ratio=None, active=None, use_attrs=False)
@example(name=None, count=None, ratio=None, active=None, use_attrs=True)
async def test_nullable_binding_preserves_valid_scalars(
    name: str | None,
    count: int | None,
    ratio: float | None,
    active: bool | None,
    use_attrs: bool,
) -> None:
    data = {"name": name, "count": count, "ratio": ratio, "active": active}
    target = NullableAttrs if use_attrs else NullableDC
    result = await http_context(method="POST", body=json.dumps(data).encode()).bind(
        target
    )
    assert (result.name, result.count, result.ratio, result.active) == (
        name,
        count,
        ratio,
        active,
    )
    assert type(result.name) is type(name)
    assert type(result.count) is type(count)
    assert type(result.active) is type(active)


@given(
    value=st.one_of(
        st.integers(), st.booleans(), st.none(), st.lists(st.integers(), max_size=5)
    ),
    use_attrs=st.booleans(),
)
@example(value=123, use_attrs=True)
async def test_string_fields_reject_non_strings(value: object, use_attrs: bool) -> None:
    target = UserAttrs if use_attrs else UserDC
    with pytest.raises(HTTPException) as error:
        await http_context(
            method="POST", body=json.dumps({"name": value}).encode()
        ).bind(target)
    assert error.value.status_code == 400


@given(name=TEXT, age=st.integers(-1000, 1000), override=st.integers(-1000, 1000))
async def test_binding_body_precedes_query_and_preserves_defaults(
    name: str,
    age: int,
    override: int,
) -> None:
    ctx = http_context(
        method="POST",
        query_string=f"age={override}".encode(),
        body=json.dumps({"name": name, "age": age}).encode(),
    )
    user = await ctx.bind(UserAttrs)
    assert user.name == name and user.age == age
    default = await http_context(
        method="POST", body=json.dumps({"name": name}).encode()
    ).bind(UserDC)
    assert default.name == name and default.role == "guest"
    pydantic_user = await http_context(
        method="POST",
        query_string=f"age={age}".encode(),
        body=json.dumps({"name": name}).encode(),
    ).bind(UserModel)
    assert pydantic_user.name == name and pydantic_user.age == age


@dataclasses.dataclass
class UnsupportedDC:
    items: list[int] = dataclasses.field(default_factory=list[int])


@given(body=st.sampled_from([b"{}", b'{"items": [1]}']))
async def test_unsupported_binding_schema_is_a_configuration_error(body: bytes) -> None:
    with pytest.raises(TypeError, match="Unsupported"):
        await http_context(method="POST", body=body).bind(UnsupportedDC)


class ParsedHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.text: list[str] = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)

    @override
    def handle_data(self, data: str) -> None:
        self.text.append(data)


@given(value=TEXT)
@example(value="<script>alert(1)</script>&\"'")
@example(value="{{other}}")
def test_renderer_preserves_text_without_interpreting_input(value: str) -> None:
    response = SimpleHTMLRenderer({"page": "<p>{{value}}</p>"}).render(
        "page", {"value": value, "other": "replaced"}
    )
    parsed = ParsedHTML()
    parsed.feed(response.body.decode())
    parsed.close()
    assert parsed.tags == ["p"]
    assert "".join(parsed.text) == value


@given(age=st.integers(-100, 150))
def test_callable_validator_enforces_predicate(age: int) -> None:
    validator = CallableValidator[int](lambda value: value >= 18)
    if age >= 18:
        validator.validate(age)
    else:
        with pytest.raises(HTTPException) as error:
            validator.validate(age)
        assert error.value.status_code == 400
