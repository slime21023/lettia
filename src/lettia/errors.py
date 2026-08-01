import typing
from typing import Any, NoReturn

from attrs import define, field

if typing.TYPE_CHECKING:
    from lettia.context import Context
    from lettia.response import Response


@define(slots=True)
class HTTPException(Exception):
    status_code: int
    detail: Any = None
    headers: dict[str, str] | None = field(default=None)

    def __str__(self) -> str:
        return f"{self.status_code}: {self.detail}"


def abort(
    status_code: int,
    detail: Any = None,
    headers: dict[str, str] | None = None,
) -> NoReturn:
    raise HTTPException(status_code=status_code, detail=detail, headers=headers)


async def default_error_handler(ctx: "Context", exc: Exception) -> "Response":
    from lettia.response import JsonResponse, TextResponse

    if isinstance(exc, HTTPException):
        headers = exc.headers or {}
        if isinstance(exc.detail, (dict, list)):
            return JsonResponse(
                data={"error": exc.detail, "status_code": exc.status_code},
                status_code=exc.status_code,
                headers=headers,
            )
        detail_msg = str(exc.detail) if exc.detail is not None else "HTTP Exception"
        return TextResponse(
            text=detail_msg,
            status_code=exc.status_code,
            headers=headers,
        )

    # Fallback for unhandled internal errors
    return TextResponse(
        text="Internal Server Error",
        status_code=500,
    )
