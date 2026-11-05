"""Single background task execution abstraction."""

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("lettia.extensions.tasks")


class BackgroundTask:
    """Represents an async or sync callable to run in the background."""

    __slots__ = ("args", "fn", "kwargs")

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    async def __call__(self) -> None:
        """Run the task; sync callables run in a thread pool.

        An awaitable returned by a sync callable (a lambda or ``functools.partial``
        around an async function, an object with ``async def __call__``) is awaited
        on the event loop, so such a task is never silently dropped.
        """
        try:
            if inspect.iscoroutinefunction(self.fn):
                result = self.fn(*self.args, **self.kwargs)
            else:
                result = await asyncio.to_thread(self.fn, *self.args, **self.kwargs)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("Error during execution of background task %r", self.fn)
