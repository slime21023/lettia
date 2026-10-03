from collections.abc import Mapping
from typing import Protocol, TypeVar, cast, runtime_checkable

from lettia._binding import (
    attrs_annotations,
    bound_kwargs,
    construct,
    dataclass_annotations,
    scalar_type,
)
from lettia.asgi import JSONValue
from lettia.context import Context
from lettia.errors import abort

T = TypeVar("T")


async def _request_data(ctx: Context) -> dict[str, JSONValue]:
    data: dict[str, JSONValue] = {}
    if ctx.method in ("POST", "PUT", "PATCH"):
        json_body = await ctx.json()
        if isinstance(json_body, dict):
            data.update(json_body)
    for key in ctx.query_params:
        if key not in data:
            data[key] = ctx.query_param(key)
    return data


@runtime_checkable
class Binder(Protocol):
    async def bind(self, ctx: Context, target_type: type[T]) -> T: ...


class AttrsBinder:
    async def bind(self, ctx: Context, target_type: type[T]) -> T:
        return await _bind_constructed(ctx, target_type, attrs_annotations(target_type))


class DataclassBinder:
    async def bind(self, ctx: Context, target_type: type[T]) -> T:
        return await _bind_constructed(
            ctx, target_type, dataclass_annotations(target_type)
        )


async def _bind_constructed[T](
    ctx: Context,
    target_type: type[T],
    annotations: Mapping[str, object],
) -> T:
    scalar_fields = {key: scalar_type(value) for key, value in annotations.items()}
    try:
        data = await _request_data(ctx)
        return construct(target_type, bound_kwargs(data, scalar_fields))
    except (TypeError, ValueError) as exc:
        abort(400, f"Failed to bind payload to {target_type.__name__}: {exc}")


class PydanticBinder:
    async def bind(self, ctx: Context, target_type: type[T]) -> T:
        try:
            import pydantic
        except ImportError as err:
            raise RuntimeError(
                "Pydantic is not installed. Install it via `uv add pydantic`."
            ) from err
        try:
            if issubclass(target_type, pydantic.BaseModel):
                model_type = cast(type[pydantic.BaseModel], target_type)
                return cast(T, model_type.model_validate(await _request_data(ctx)))
            adapter: pydantic.TypeAdapter[object] = pydantic.TypeAdapter(target_type)
            return cast(T, adapter.validate_python(await _request_data(ctx)))
        except pydantic.ValidationError as exc:
            abort(400, exc.errors())
