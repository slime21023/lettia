"""Asynchronous and synchronous teardown for scoped services."""

import inspect
import logging
from typing import Any

import rodi

logger = logging.getLogger("lettia.extensions.di")


async def teardown_scope(scope: rodi.ActivationScope) -> None:
    """Clean up all scoped instances by calling aclose() or close() if present."""
    services_map = getattr(scope, "scoped_services", None)
    if not services_map:
        return

    # Teardown in reverse allocation order (LIFO)
    instances: list[Any] = list(services_map.values())
    for instance in reversed(instances):
        aclose = getattr(instance, "aclose", None)
        if callable(aclose):
            try:
                res = aclose()
                if inspect.isawaitable(res):
                    await res
            except Exception:
                logger.exception("Error during async teardown of %r", instance)
            continue

        close = getattr(instance, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.exception("Error during sync teardown of %r", instance)
