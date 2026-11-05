# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Extended rodi.Container providing wire() integration with Lettia App."""

import ast
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
        """Bind Injected descriptors, resolve services, and mount into App.

        A service is built once and shared by every request, so its constructor
        may only depend on singletons and transients. A scoped dependency would be
        created here, outside any request, and then shared by all of them; that is
        reported as a startup problem and the service is not mounted. Declare such
        a dependency as ``Injected[T]``. The check stops resolution before the
        scoped dependency is created, so no factory runs.
        """
        for service_cls in services:
            self._bind_injected_descriptors(service_cls)
            if service_cls not in self._map:
                self.register(service_cls)

        provider = self.build_provider()
        for service_cls in services:
            scope = provider.create_scope()
            scope.scoped_services = _ScopedProbe(service_cls)
            try:
                instance = cast(object, provider.get(service_cls, scope))
            except _ScopedDependency as found:
                dependency = getattr(found.dependency, "__qualname__", found.dependency)
                app.add_startup_problem(
                    f"{service_cls.__qualname__} depends on scoped {dependency} "
                    "through its constructor, so one instance would be shared by "
                    "every request; declare it as Injected[T] instead"
                )
                continue
            finally:
                scope.dispose()  # type: ignore[no-untyped-call]
            mount(app, instance)

    @staticmethod
    def _bind_injected_descriptors(target_cls: type) -> None:
        """Install an ``Injected`` descriptor for every annotated declaration.

        Declarations are found on the class and its bases, as ``Injected[T]`` or
        ``ClassVar[Injected[T]]`` (the form that a dataclass does not turn into a
        constructor parameter), written as objects or as strings. A declaration
        without ``T`` cannot be resolved and raises ``TypeError``.
        """
        for klass in reversed(target_cls.__mro__):
            if klass is object:
                continue
            for name, hint in inspect.get_annotations(klass).items():
                if name in klass.__dict__:
                    continue
                service = _injected_service(klass, name, hint)
                if service is _NOT_INJECTED:
                    continue
                if service is None:
                    raise TypeError(
                        f"{klass.__qualname__}.{name} is annotated with a bare "
                        "Injected; write Injected[T] so the service type is known"
                    )
                desc: Injected[Any] = Injected(service)
                setattr(klass, name, desc)
                desc.__set_name__(klass, name)


class _ScopedDependency(Exception):
    """Raised by the probe scope when resolution reaches a scoped dependency."""

    def __init__(self, dependency: Any) -> None:
        super().__init__(dependency)
        self.dependency = dependency


class _ScopedProbe(dict[Any, Any]):
    """Scoped services that refuse to create anything.

    rodi asks ``type in scope.scoped_services`` before it builds a scoped
    service, so raising there stops resolution before any factory runs. The
    service being wired may itself be registered as scoped.
    """

    def __init__(self, allowed: type) -> None:
        super().__init__()
        self._allowed = allowed

    @override
    def __contains__(self, key: object) -> bool:
        if key is self._allowed:
            return False
        raise _ScopedDependency(key)


_NOT_INJECTED: Any = object()


def _injected_service(owner: type, name: str, hint: Any) -> Any:
    """The ``T`` of ``Injected[T]`` (``None`` when bare); else ``_NOT_INJECTED``."""
    if isinstance(hint, str):
        try:
            hint = _resolve(owner, name, hint)
        except NameError as exc:
            if _declares_injected(owner, name, hint):
                raise TypeError(
                    f"cannot resolve the Injected annotation {name!r} of "
                    f"{owner.__qualname__}: {exc}"
                ) from None
            return _NOT_INJECTED
    if typing.get_origin(hint) is typing.ClassVar:
        (hint,) = typing.get_args(hint) or (None,)
    if hint is Injected:
        return None
    if typing.get_origin(hint) is Injected:
        args = typing.get_args(hint)
        return args[0] if args else None
    return _NOT_INJECTED


def _resolve(owner: type, name: str, text: str) -> Any:
    """Evaluate the annotation ``text`` in the module of ``owner``.

    ``typing.get_type_hints`` accepts ``ClassVar[...]`` only on a class, and
    resolves names in the module of that class, so a stand-in class carries it.
    """
    stand_in = type(
        "_Declaration",
        (),
        {"__annotations__": {name: text}, "__module__": owner.__module__},
    )
    hints = typing.get_type_hints(
        stand_in, localns=dict(vars(owner)), include_extras=True
    )
    return hints[name]


def _declares_injected(owner: type, name: str, text: str) -> bool:
    """Whether the annotation text is ``Injected[...]`` or ``ClassVar[Injected[...]]``.

    Only the name in front of the brackets is resolved, so an alias counts, even
    when the type inside the brackets cannot be resolved.
    """
    try:
        node = ast.parse(text, mode="eval").body
    except SyntaxError:
        return False
    if isinstance(node, ast.Subscript) and ast.unparse(node.value).endswith("ClassVar"):
        node = node.slice
    if isinstance(node, ast.Subscript):
        node = node.value
    try:
        return _resolve(owner, name, ast.unparse(node)) is Injected
    except NameError:
        return False
