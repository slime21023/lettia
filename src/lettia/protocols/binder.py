import dataclasses
import types
import typing
from collections.abc import Callable, Mapping
from typing import Protocol, TypeVar, cast, runtime_checkable

from attrs import fields, has

from lettia.asgi import JSONValue
from lettia.context import Context
from lettia.errors import abort

T = TypeVar("T")


def _coerce_type(value: JSONValue, target_type: object) -> object:
    origin = typing.get_origin(target_type)
    if origin in (typing.Union, types.UnionType):
        non_none_args = [
            argument
            for argument in typing.get_args(target_type)
            if argument is not type(None)
        ]
        if len(non_none_args) == 1:
            target_type = non_none_args[0]
    if target_type is int:
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError as exc:
                raise ValueError(f"Expected an integer, got {value!r}") from exc
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Expected an integer, got {value!r}")
    if target_type is float:
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError as exc:
                raise ValueError(f"Expected a number, got {value!r}") from exc
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Expected a number, got {value!r}")
        return float(value)
    if target_type is bool:
        if isinstance(value, str):
            normalized = value.lower()
            if normalized in ("true", "1", "yes"):
                return True
            if normalized in ("false", "0", "no"):
                return False
            raise ValueError(f"Expected a boolean, got {value!r}")
        if not isinstance(value, bool):
            raise ValueError(f"Expected a boolean, got {value!r}")
    return value


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


def _bound_kwargs(
    data: Mapping[str, JSONValue], annotations: Mapping[str, object]
) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    for key, value in data.items():
        target_type = annotations.get(key)
        if target_type is not None:
            kwargs[key] = _coerce_type(value, target_type)
    return kwargs


def _construct[T](target_type: type[T], kwargs: Mapping[str, object]) -> T:
    constructor = cast(Callable[..., T], target_type)
    return constructor(**kwargs)


@runtime_checkable
class Binder(Protocol):
    async def bind(self, ctx: Context, target_type: type[T]) -> T: ...


class AttrsBinder:
    async def bind(self, ctx: Context, target_type: type[T]) -> T:
        if not has(target_type):
            raise TypeError(f"Target type {target_type} is not an attrs class")
        try:
            resolved_annotations: dict[str, object] = typing.get_type_hints(target_type)
        except (NameError, TypeError):
            resolved_annotations = {}
        annotations = {
            attribute.name: resolved_annotations.get(attribute.name, attribute.type)
            for attribute in fields(target_type)
        }
        return cast(T, await _bind_constructed(ctx, target_type, annotations))


class DataclassBinder:
    async def bind(self, ctx: Context, target_type: type[T]) -> T:
        if not dataclasses.is_dataclass(target_type):
            raise TypeError(f"Target type {target_type} is not a dataclass")
        try:
            resolved_annotations: dict[str, object] = typing.get_type_hints(
                target_type
            )
        except (NameError, TypeError):
            resolved_annotations = {}
        annotations = {
            attribute.name: resolved_annotations.get(attribute.name, attribute.type)
            for attribute in dataclasses.fields(target_type)
        }
        return await _bind_constructed(ctx, target_type, annotations)


async def _bind_constructed[T](
    ctx: Context,
    target_type: type[T],
    annotations: Mapping[str, object],
) -> T:
    try:
        data = await _request_data(ctx)
        return _construct(target_type, _bound_kwargs(data, annotations))
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
