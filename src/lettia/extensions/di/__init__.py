"""Dependency Injection extension for Lettia backed by Rodi."""

from lettia.extensions.di.container import Container
from lettia.extensions.di.descriptor import (
    Injected,
    current_scope,
    get_current_scope,
)
from lettia.extensions.di.middleware import SCOPE_KEY, DIMiddleware
from lettia.extensions.di.teardown import teardown_scope

__all__ = [
    "Container",
    "DIMiddleware",
    "Injected",
    "SCOPE_KEY",
    "current_scope",
    "get_current_scope",
    "teardown_scope",
]
