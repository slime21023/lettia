from collections.abc import Callable
from typing import Any

from attrs import define


@define(slots=True, frozen=True)
class Route:
    method: str
    path: str
    handler: Callable[..., Any]
    name: str | None = None
