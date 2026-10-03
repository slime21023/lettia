import typing
from typing import NoReturn, override

from attrs import define, field

from lettia._json import validate_json_value
from lettia.asgi import JSONValue

if typing.TYPE_CHECKING:
    from lettia.context import Context
    from lettia.response import Response


@define(slots=True)
class HTTPException(Exception):
    status_code: int
    detail: object = None
    headers: dict[str, str] | None = field(default=None)

    @override
    def __str__(self) -> str:
        return f"{self.status_code}: {self.detail}"


def abort(
    status_code: int,
    detail: object = None,
    headers: dict[str, str] | None = None,
) -> NoReturn:
    raise HTTPException(status_code=status_code, detail=detail, headers=headers)


async def default_error_handler(ctx: "Context", exc: Exception) -> "Response":
    from lettia.response import JsonResponse, TextResponse

    if isinstance(exc, HTTPException):
        headers = exc.headers or {}
        detail = _json_container(exc.detail)
        if detail is not None:
            return JsonResponse(
                data={
                    "error": detail,
                    "status_code": exc.status_code,
                },
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


def _json_container(value: object) -> dict[str, JSONValue] | list[JSONValue] | None:
    try:
        validated = validate_json_value(value)
    except TypeError:
        return None
    if isinstance(validated, (dict, list)):
        return validated
    return None
