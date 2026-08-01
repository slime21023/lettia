from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from lettia.errors import HTTPException, abort


@runtime_checkable
class Validator(Protocol):
    def validate(self, obj: Any) -> None: ...


class CallableValidator:
    def __init__(self, func: Callable[[Any], bool | None]) -> None:
        self.func = func

    def validate(self, obj: Any) -> None:
        try:
            result = self.func(obj)
            if result is False:
                abort(400, "Validation failed")
        except HTTPException:
            raise
        except Exception as exc:
            abort(400, f"Validation error: {exc}")
