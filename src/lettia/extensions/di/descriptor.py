"""Declarative property injection descriptor backed by rodi ActivationScope."""

import typing
from contextvars import ContextVar
from typing import Any, Self, overload

import rodi

current_scope: ContextVar[rodi.ActivationScope | None] = ContextVar(
    "lettia_di_current_scope", default=None
)


def get_current_scope() -> rodi.ActivationScope | None:
    """Return the currently active request scope from the context variable."""
    return current_scope.get()


class Injected[T]:
    """Property descriptor that resolves a dependency from the current request scope."""

    __slots__ = ("_attribute_name", "_service_type")

    def __init__(self, service_type: type[T] | None = None) -> None:
        self._service_type = service_type
        self._attribute_name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self._attribute_name = name
        if self._service_type is None:
            annotations = getattr(owner, "__annotations__", {})
            hint = annotations.get(name)
            if hint is not None:
                origin = typing.get_origin(hint)
                if origin is Injected:
                    args = typing.get_args(hint)
                    if args and isinstance(args[0], type):
                        self._service_type = args[0]
                elif isinstance(hint, type):
                    self._service_type = hint

    @property
    def service_type(self) -> type[T] | None:
        """The resolved service type bound to this descriptor."""
        return self._service_type

    @overload
    def __get__(self, instance: None, owner: Any) -> Self: ...

    @overload
    def __get__(self, instance: object, owner: Any) -> T: ...

    def __get__(self, instance: object | None, owner: Any = None) -> Any:
        if instance is None:
            return self

        scope = current_scope.get()
        if scope is None:
            raise RuntimeError(
                f"Cannot resolve Injected descriptor '{self._attribute_name}' "
                "outside of an active request scope"
            )

        if self._service_type is None:
            raise TypeError(
                f"Injected descriptor '{self._attribute_name}' "
                "has no resolved service type"
            )

        return scope.get(self._service_type)
