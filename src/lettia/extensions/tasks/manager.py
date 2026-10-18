"""BackgroundTasks collection and Context resolution."""

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, Self

from lettia.core import Context, Key
from lettia.extensions.tasks.task import BackgroundTask


class BackgroundTasks:
    """Collects tasks to execute after an HTTP or WebSocket response is returned."""

    __slots__ = ("_tasks",)

    def __init__(self) -> None:
        self._tasks: list[BackgroundTask] = []

    def add_task(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Self:
        """Add a callable to be executed sequentially in the background."""
        self._tasks.append(BackgroundTask(fn, *args, **kwargs))
        return self

    def __bool__(self) -> bool:
        """Return True if any tasks have been scheduled."""
        return bool(self._tasks)

    def __len__(self) -> int:
        """Return the number of scheduled tasks."""
        return len(self._tasks)

    async def run(self) -> None:
        """Execute all scheduled tasks sequentially in FIFO order."""
        for task in self._tasks:
            await task()

    @classmethod
    def of(cls, c: Context) -> "BackgroundTasks":
        """Resolve the active BackgroundTasks instance from Context."""
        tasks = TASKS_KEY.get(c, None)
        if tasks is None:
            raise RuntimeError(
                "BackgroundTasks is not available on this request context; "
                "make sure BackgroundTasksMiddleware is mounted on the App."
            )
        return tasks

    @classmethod
    def __injected__(cls) -> "BackgroundTasks":
        """Resolver hook for lettia.extensions.di Injected[BackgroundTasks]."""
        tasks = current_tasks.get()
        if tasks is None:
            raise RuntimeError(
                "BackgroundTasks is not available on this request context; "
                "make sure BackgroundTasksMiddleware is mounted on the App."
            )
        return tasks


TASKS_KEY = Key[BackgroundTasks]("lettia.extensions.tasks")
"""Key for accessing the current BackgroundTasks instance on Context.state."""

current_tasks: ContextVar[BackgroundTasks | None] = ContextVar(
    "lettia_tasks_current", default=None
)
