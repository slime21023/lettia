# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Extended rodi.Container providing wire() integration with Lettia App."""

import inspect
import typing
from collections.abc import Sequence
from typing import Any, Self, cast, override

import rodi

from lettia.contract.mount import MountTarget, mount
from lettia.extensions.di.descriptor import Injected


class Container(rodi.Container):
    """Dependency injection container extending rodi.Container with Lettia wire()."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    @override
    def build_provider(self) -> rodi.Services:
        """Build and cache the rodi.Services provider."""
        if self._provider is None:
            self._provider = super().build_provider()
        return self._provider

    @override
    def add_singleton[T](
        self, base_type: type[T], concrete_type: type[T] | T | None = None
    ) -> Self:
        """Register a singleton type or pre-constructed instance."""
        if concrete_type is not None and not inspect.isclass(concrete_type):
            self.add_instance(concrete_type, base_type)
            return self
        super().add_singleton(base_type, concrete_type)
        return self

    def wire(self, app: MountTarget, services: Sequence[type]) -> None:
        """Bind Injected descriptors, resolve services, and mount into App."""
        for service_cls in services:
            self._bind_injected_descriptors(service_cls)
            if service_cls not in self._map:
                self.register(service_cls)

        provider = self.build_provider()
        for service_cls in services:
            instance = cast(object, provider.get(service_cls))
            mount(app, instance)

    @staticmethod
    def _bind_injected_descriptors(target_cls: type) -> None:
        annotations = getattr(target_cls, "__annotations__", {})
        for name, hint in annotations.items():
            origin = typing.get_origin(hint)
            if origin is Injected or hint is Injected:
                if name not in target_cls.__dict__:
                    args = typing.get_args(hint)
                    service_type = (
                        args[0] if args and isinstance(args[0], type) else None
                    )
                    desc: Injected[Any] = Injected(service_type)
                    setattr(target_cls, name, desc)
                    desc.__set_name__(target_cls, name)
