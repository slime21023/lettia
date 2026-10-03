from __future__ import annotations

import dataclasses
import json
import sys

import pytest
from attrs import define, field
from hypothesis import example, given
from hypothesis import strategies as st
from pydantic import BaseModel, field_validator

from lettia import App, Context, JSONValue
from lettia.errors import HTTPException
from lettia.middleware import body_limit, cors, request_id
from lettia.protocols import (
    AttrsBinder,
    DataclassBinder,
    PydanticBinder,
)
from lettia.response import JsonResponse
from lettia.testing import TestClient
from tests.support.asgi import (
    http_context,
)
from tests.support.strategies import TEXT


@pytest.mark.contract("BIND-PYDANTIC", "BIND-SCHEMA")
@pytest.mark.parametrize("smart", [False, True])
@pytest.mark.parametrize("error_type", [TypeError, ImportError])
async def test_pydantic_validator_programming_error_is_not_reclassified(
    smart: bool,
    error_type: type[Exception],
) -> None:
    failure = error_type("validator implementation failed")

    class Payload(BaseModel):
        value: int

        @field_validator("value")
        @classmethod
        def validate_value(cls, value: int) -> int:
            raise failure

    ctx = http_context(method="POST", body=b'{"value": 1}')
    with pytest.raises(error_type) as error:
        if smart:
            await ctx.bind(Payload)
        else:
            await PydanticBinder().bind(ctx, Payload)
    assert error.value is failure


@dataclasses.dataclass
class InitOnlyRequest:
    value: dataclasses.InitVar[int]
    result: int = dataclasses.field(init=False)

    def __post_init__(self, value: int) -> None:
        self.result = value


@define
class AttrsRequest:
    value: int


class PydanticRequest(BaseModel):
    value: int


@define(slots=True)
class UserPayload:
    name: str
    age: int = 18


@dataclasses.dataclass
class InitOnlyBase:
    required: dataclasses.InitVar[int]
    ignored: list[int] | None = dataclasses.field(init=False, default=None)


@dataclasses.dataclass(kw_only=True)
class InitOnlyPayload(InitOnlyBase):
    optional: dataclasses.InitVar[int | None] = 7
    observed: tuple[int, int | None] = dataclasses.field(init=False)

    def __post_init__(self, required: int, optional: int | None) -> None:
        self.observed = (required, optional)


@dataclasses.dataclass
class UnsupportedInitOnly:
    items: dataclasses.InitVar[list[int]]


@define(slots=True)
class AliasedPayload:
    _name: str
    age: int = field(alias="years", default=18)
    internal: list[int] = field(init=False, factory=list[int])


@dataclasses.dataclass
class ConstructedPayload:
    name: str
    age: int = 18
    internal: list[int] = dataclasses.field(init=False, default_factory=list[int])


@define(slots=True)
class FloatAttrs:
    value: float


@dataclasses.dataclass
class FloatDC:
    value: float


@define(slots=True)
class UserAttrs:
    name: str
    age: int = 18


@dataclasses.dataclass
class CoercedPayload:
    enabled: bool
    ratio: float
    optional_count: int | None = None


@dataclasses.dataclass
class UserDC:
    name: str
    role: str = "guest"


class UserModel(BaseModel):
    name: str
    age: int = 18


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


@dataclasses.dataclass
class UnsupportedDC:
    items: list[int] = dataclasses.field(default_factory=list[int])


@pytest.mark.contract("BIND-CONSTRUCT", "REQ-JSON")
@pytest.mark.parametrize("reader", ["json", "dataclass", "attrs", "pydantic"])
def test_json_depth_errors_share_route_binding_and_middleware_behavior(
    reader: str,
) -> None:
    app = App()
    app.use(
        cors(allow_origins=["https://example.com"]),
        request_id(generator=lambda: "depth-test"),
        body_limit(64 * 1024),
    )

    @app.post("/")
    async def route(ctx: Context) -> dict[str, JSONValue]:
        if reader == "dataclass":
            return {"value": (await ctx.bind(InitOnlyRequest)).result}
        if reader == "attrs":
            return {"value": (await ctx.bind(AttrsRequest)).value}
        if reader == "pydantic":
            return {"value": (await ctx.bind(PydanticRequest)).value}
        return {"value": await ctx.json()}

    client = TestClient(app)
    depth = sys.getrecursionlimit() + 100
    result = client.post(
        "/",
        content="[" * depth + "0" + "]" * depth,
        headers={"Origin": "https://example.com"},
    )
    assert result.status_code == 400 and "nesting depth" in result.text
    assert result.headers["access-control-allow-origin"] == "https://example.com"
    assert result.headers["x-request-id"] == "depth-test"
    valid = client.post("/", json={"value": 42})
    assert valid.status_code == 200
    assert valid.json() == {"value": {"value": 42} if reader == "json" else 42}


@pytest.mark.contract("BIND-CONSTRUCT", "APP-COMPLETE")
def test_smart_bind_and_background_task() -> None:
    background_ran = False
    app = App()

    def set_bg_flag(name: str) -> None:
        nonlocal background_ran
        background_ran = True

    @app.post("/users")
    async def create_user(ctx: Context) -> JsonResponse:
        user = await ctx.bind(UserPayload)
        ctx.add_background_task(set_bg_flag, user.name)
        return JsonResponse({"name": user.name, "age": user.age}, status_code=201)

    client = TestClient(app)
    response = client.post("/users?age=20", json={"name": "Charlie"})

    assert response.status_code == 201
    assert response.json() == {"name": "Charlie", "age": 20}
    assert background_ran


@pytest.mark.contract("BIND-CONSTRUCT")
@given(
    required=st.integers(-1000, 1000),
    optional=st.one_of(st.none(), st.integers(-1000, 1000)),
    body_input=st.booleans(),
    smart=st.booleans(),
)
async def test_dataclass_initvars_follow_constructor_and_shared_binding_rules(
    required: int, optional: int | None, body_input: bool, smart: bool
) -> None:
    from urllib.parse import urlencode

    data = {"required": required, "optional": optional}
    query = {"required": str(required), "ignored": "untrusted"}
    if optional is not None:
        query["optional"] = str(optional)
    ctx = http_context(
        method="POST",
        body=json.dumps(data).encode() if body_input else b"",
        query_string=urlencode(
            {"required": "9999", "optional": "9999"} if body_input else query
        ).encode(),
    )
    bound = (
        await ctx.bind(InitOnlyPayload)
        if smart
        else await DataclassBinder().bind(ctx, InitOnlyPayload)
    )
    expected_optional = optional if body_input or optional is not None else 7
    assert bound.observed == (required, expected_optional)


@pytest.mark.contract("BIND-CONSTRUCT")
@pytest.mark.parametrize("body", [b"{}", b'{"required":"invalid"}'])
async def test_dataclass_initvars_report_missing_and_invalid_input(body: bytes) -> None:
    with pytest.raises(HTTPException) as error:
        await http_context(method="POST", body=body).bind(InitOnlyPayload)
    assert error.value.status_code == 400


@pytest.mark.contract("BIND-SCHEMA")
@pytest.mark.parametrize("body", [b"{}", b'{"items":[1]}'])
async def test_unsupported_initvar_annotation_is_a_configuration_error(
    body: bytes,
) -> None:
    with pytest.raises(TypeError, match="Unsupported binding annotation"):
        await http_context(method="POST", body=body).bind(UnsupportedInitOnly)


@pytest.mark.contract("BIND-CONSTRUCT")
@given(
    name=TEXT,
    age=st.integers(-1000, 1000),
    attrs_model=st.booleans(),
    smart=st.booleans(),
    body_input=st.booleans(),
)
async def test_binders_constructor_fields_preserve_aliases_and_ignore_internal_fields(
    name: str,
    age: int,
    attrs_model: bool,
    smart: bool,
    body_input: bool,
) -> None:
    from urllib.parse import urlencode

    age_key = "years" if attrs_model else "age"
    data = {"name": name, age_key: str(age), "internal": "untrusted"}
    ctx = http_context(
        method="POST",
        body=json.dumps(data).encode() if body_input else b"",
        query_string=urlencode(
            {"name": "fallback", age_key: "99"} if body_input else data
        ).encode(),
    )
    if attrs_model:
        value = (
            await ctx.bind(AliasedPayload)
            if smart
            else await AttrsBinder().bind(ctx, AliasedPayload)
        )
        actual_name = value._name
        actual_age = value.age
        internal = value.internal
    else:
        value_dc = (
            await ctx.bind(ConstructedPayload)
            if smart
            else await DataclassBinder().bind(ctx, ConstructedPayload)
        )
        actual_name = value_dc.name
        actual_age = value_dc.age
        internal = value_dc.internal

    assert (actual_name, actual_age, internal) == (name, age, [])


@pytest.mark.contract("BIND-CONSTRUCT")
async def test_attrs_binder_alias_is_the_only_public_input_name() -> None:
    value = await http_context(
        method="POST", body=b'{"name":"ok","age":99,"_name":"ignored"}'
    ).bind(AliasedPayload)
    assert value._name == "ok" and value.age == 18
    with pytest.raises(HTTPException, match="Failed to bind"):
        await http_context(method="POST", body=b'{"_name":"not an alias"}').bind(
            AliasedPayload
        )


@pytest.mark.contract("BIND-SCALAR")
@given(
    exponent=st.integers(309, 1000),
    negative=st.booleans(),
    attrs_model=st.booleans(),
    smart=st.booleans(),
)
async def test_binders_float_overflow_is_a_client_error(
    exponent: int,
    negative: bool,
    attrs_model: bool,
    smart: bool,
) -> None:
    number = 10**exponent * (-1 if negative else 1)
    ctx = http_context(method="POST", body=json.dumps({"value": number}).encode())
    with pytest.raises(HTTPException, match="float range") as error:
        if attrs_model:
            if smart:
                await ctx.bind(FloatAttrs)
            else:
                await AttrsBinder().bind(ctx, FloatAttrs)
        elif smart:
            await ctx.bind(FloatDC)
        else:
            await DataclassBinder().bind(ctx, FloatDC)
    assert error.value.status_code == 400


@pytest.mark.contract("BIND-SCALAR")
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


@pytest.mark.contract("BIND-SCALAR")
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


@pytest.mark.contract("BIND-SCHEMA")
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


@pytest.mark.contract("BIND-PYDANTIC")
@pytest.mark.asyncio
async def test_pydantic_binder_reports_validation_errors() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await PydanticBinder().bind(
            http_context(method="POST", body=b'{"name": 1}'), UserModel
        )
    assert exc_info.value.status_code == 400


@pytest.mark.contract("BIND-CONSTRUCT")
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


@pytest.mark.contract("BIND-SCALAR")
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


@pytest.mark.contract("BIND-SCALAR")
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


@pytest.mark.contract("BIND-CONSTRUCT")
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


@pytest.mark.contract("BIND-SCHEMA")
@given(body=st.sampled_from([b"{}", b'{"items": [1]}']))
async def test_unsupported_binding_schema_is_a_configuration_error(body: bytes) -> None:
    with pytest.raises(TypeError, match="Unsupported"):
        await http_context(method="POST", body=body).bind(UnsupportedDC)
