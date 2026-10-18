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
        """Run the task, delegating sync callables to a thread pool."""
        try:
            if inspect.iscoroutinefunction(self.fn):
                res = self.fn(*self.args, **self.kwargs)
                if inspect.isawaitable(res):
                    await res
            else:
                await asyncio.to_thread(self.fn, *self.args, **self.kwargs)
        except Exception:
            logger.exception("Error during execution of background task %r", self.fn)
