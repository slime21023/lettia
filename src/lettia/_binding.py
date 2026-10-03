import dataclasses
import inspect
import types
import typing
from collections.abc import Callable, Mapping
from typing import cast

from attrs import fields, has

from lettia.asgi import JSONValue


def scalar_type(target_type: object) -> tuple[object, bool]:
    nullable = False
    origin = typing.get_origin(target_type)
    if origin in (typing.Union, types.UnionType):
        non_none_args = [
            argument
            for argument in typing.get_args(target_type)
            if argument is not type(None)
        ]
        if len(non_none_args) == 1 and type(None) in typing.get_args(target_type):
            target_type = non_none_args[0]
            nullable = True
    if (
        target_type is not str
        and target_type is not int
        and target_type is not float
        and target_type is not bool
    ):
        raise TypeError(
            f"Unsupported binding annotation {target_type!r}; use PydanticBinder "
            "for complex models"
        )
    return target_type, nullable


def coerce_type(value: JSONValue, target_type: object, nullable: bool) -> object:
    if nullable and value is None:
        return None
    if target_type is str and not isinstance(value, str):
        raise ValueError(f"Expected a string, got {value!r}")
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
        try:
            return float(value)
        except OverflowError as exc:
            raise ValueError(f"Number is outside the float range: {value!r}") from exc
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


def bound_kwargs(
    data: Mapping[str, JSONValue], annotations: Mapping[str, tuple[object, bool]]
) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    for key, value in data.items():
        target_type = annotations.get(key)
        if target_type is not None:
            kwargs[key] = coerce_type(value, *target_type)
    return kwargs


def construct[T](target_type: type[T], kwargs: Mapping[str, object]) -> T:
    constructor = cast(Callable[..., T], target_type)
    return constructor(**kwargs)


def attrs_annotations(target_type: type[object]) -> dict[str, object]:
    if not has(target_type):
        raise TypeError(f"Target type {target_type} is not an attrs class")
    try:
        resolved_annotations: dict[str, object] = typing.get_type_hints(target_type)
    except (NameError, TypeError):
        resolved_annotations = {}
    annotations = {
        attribute.alias: resolved_annotations.get(attribute.name, attribute.type)
        for attribute in fields(target_type)
        if attribute.init
    }
    return annotations


def dataclass_annotations(target_type: type[object]) -> dict[str, object]:
    if not dataclasses.is_dataclass(target_type):
        raise TypeError(f"Target type {target_type} is not a dataclass")
    try:
        resolved_annotations: dict[str, object] = typing.get_type_hints(target_type)
    except (NameError, TypeError):
        resolved_annotations = {}
    annotations = {
        attribute.name: resolved_annotations.get(attribute.name, attribute.type)
        for attribute in dataclasses.fields(target_type)
        if attribute.init
    }
    # fields() omits InitVar; only include those accepted by the constructor.
    for name, parameter in inspect.signature(target_type).parameters.items():
        annotation = resolved_annotations.get(name, parameter.annotation)
        if isinstance(annotation, dataclasses.InitVar):
            # InitVar's runtime wrapper cannot be parameterized in type casts.
            annotations[name] = annotation.type  # pyright: ignore[reportUnknownMemberType]
    return annotations
