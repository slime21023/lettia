import dataclasses
import types
import typing
from typing import Any, Protocol, TypeVar, runtime_checkable

from attrs import fields, has

from lettia.context import Context
from lettia.errors import abort

T = TypeVar("T")


def _coerce_type(value: Any, target_type: Any) -> Any:
    if value is None or target_type is Any:
        return value

    origin = typing.get_origin(target_type)
    if origin in (typing.Union, types.UnionType):
        args = typing.get_args(target_type)
        non_none_args = [arg for arg in args if arg is not type(None)]
        if len(non_none_args) == 1:
            target_type = non_none_args[0]

    if target_type is int and isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"Expected an integer, got {value!r}") from exc
    if target_type is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Expected an integer, got {value!r}")
        return value
    if target_type is float and isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"Expected a number, got {value!r}") from exc
    if target_type is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Expected a number, got {value!r}")
        return float(value)
    if target_type is bool and isinstance(value, str):
        normalized = value.lower()
        if normalized in ("true", "1", "yes"):
            return True
        if normalized in ("false", "0", "no"):
            return False
        raise ValueError(f"Expected a boolean, got {value!r}")
    if target_type is bool:
        if not isinstance(value, bool):
            raise ValueError(f"Expected a boolean, got {value!r}")
        return value

    return value


@runtime_checkable
class Binder(Protocol):
    async def bind(self, ctx: Context, target_type: type[T]) -> T: ...


class AttrsBinder:
    async def bind(self, ctx: Context, target_type: type[T]) -> T:
        if not has(target_type):
            raise TypeError(f"Target type {target_type} is not an attrs class")

        data: dict[str, Any] = {}
        if ctx.method in ("POST", "PUT", "PATCH"):
            json_body = await ctx.json()
            if isinstance(json_body, dict):
                data.update(json_body)

        for k in ctx.query_params:
            if k not in data:
                data[k] = ctx.query_param(k)

        field_map = {f.name: f for f in fields(target_type)}
        kwargs: dict[str, Any] = {}

        try:
            for k, v in data.items():
                if k in field_map:
                    f_type = field_map[k].type
                    kwargs[k] = _coerce_type(v, f_type) if f_type else v
        except ValueError as exc:
            abort(400, f"Failed to bind payload to {target_type.__name__}: {exc}")

        try:
            return target_type(**kwargs)
        except (TypeError, ValueError) as exc:
            abort(400, f"Failed to bind payload to {target_type.__name__}: {exc}")


class DataclassBinder:
    async def bind(self, ctx: Context, target_type: type[T]) -> T:
        if not dataclasses.is_dataclass(target_type):
            raise TypeError(f"Target type {target_type} is not a dataclass")

        data: dict[str, Any] = {}
        if ctx.method in ("POST", "PUT", "PATCH"):
            json_body = await ctx.json()
            if isinstance(json_body, dict):
                data.update(json_body)

        for k in ctx.query_params:
            if k not in data:
                data[k] = ctx.query_param(k)

        field_map = {f.name: f for f in dataclasses.fields(target_type)}
        kwargs: dict[str, Any] = {}

        try:
            for k, v in data.items():
                if k in field_map:
                    f_type = field_map[k].type
                    kwargs[k] = _coerce_type(v, f_type) if f_type else v
        except ValueError as exc:
            abort(400, f"Failed to bind payload to {target_type.__name__}: {exc}")

        try:
            return target_type(**kwargs)
        except (TypeError, ValueError) as exc:
            abort(400, f"Failed to bind payload to {target_type.__name__}: {exc}")


class PydanticBinder:
    async def bind(self, ctx: Context, target_type: type[T]) -> T:
        try:
            import pydantic  # pyright: ignore[reportMissingImports]
        except ImportError as err:
            raise RuntimeError(
                "Pydantic is not installed. Install it via `uv add pydantic`."
            ) from err

        data: dict[str, Any] = {}
        if ctx.method in ("POST", "PUT", "PATCH"):
            json_body = await ctx.json()
            if isinstance(json_body, dict):
                data.update(json_body)

        for k in ctx.query_params:
            if k not in data:
                data[k] = ctx.query_param(k)

        try:
            pydantic_type = typing.cast(Any, target_type)
            if issubclass(pydantic_type, pydantic.BaseModel):
                return typing.cast(T, pydantic_type.model_validate(data))
            return typing.cast(
                T, pydantic.TypeAdapter(pydantic_type).validate_python(data)
            )
        except pydantic.ValidationError as exc:
            abort(400, exc.errors())
