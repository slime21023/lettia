from collections.abc import Callable
from typing import Protocol, TypeVar, override, runtime_checkable

from lettia.errors import HTTPException, abort

T = TypeVar("T", contravariant=True)


@runtime_checkable
class Validator(Protocol[T]):
    def validate(self, obj: T) -> None: ...


class CallableValidator(Validator[T]):
    def __init__(self, func: Callable[[T], bool | None]) -> None:
        self.func: Callable[[T], bool | None] = func

    @override
    def validate(self, obj: T) -> None:
        try:
            result = self.func(obj)
            if result is False:
                abort(400, "Validation failed")
        except HTTPException:
            raise
        except Exception as exc:
            abort(400, f"Validation error: {exc}")
