"""Lightweight in-process background tasks extension for Lettia."""

from lettia.extensions.tasks.manager import TASKS_KEY, BackgroundTasks
from lettia.extensions.tasks.middleware import BackgroundTasksMiddleware

__all__ = [
    "BackgroundTasks",
    "BackgroundTasksMiddleware",
    "TASKS_KEY",
]
