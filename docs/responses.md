---
title: Responses and Errors
---

# Responses and errors

Return ordinary values for simple output; use a response object when you need
to control status, headers, cookies or streaming. These are application-facing
examples; exact signatures and protocol rules live in the
[response reference](api/response.md).

## Choose a response

| Return value | Result |
|---|---|
| `dict` or `list` containing JSON values | JSON response |
| `str` | Text response |
| `bytes` | Binary response |
| `(body, status_code)` or `(body, status_code, headers)` | Body with explicit status and optional headers |
| `JsonResponse`, `TextResponse` or `Response` | Explicit response configuration |
| `StreamResponse` | Async byte stream |

After `uv add lettia uvicorn`, save this as `app.py`:

```python
from lettia import App, Context, JsonResponse

app = App()


@app.post("/items")
def create_item(ctx: Context) -> JsonResponse:
    return JsonResponse(
        {"id": "item-1"},
        status_code=201,
        headers={"Location": "/items/item-1"},
    )
```

Run `uv run uvicorn app:app --reload`. POST `/items` returns HTTP 201,
`{"id":"item-1"}`, and a Location header. This example creates a response only;
it does not persist an item or define a route at the Location URL.

## Headers and cookies

Add this handler to the same `app.py`:

```python
@app.get("/preferences")
def preferences(ctx: Context) -> JsonResponse:
    response = JsonResponse({"theme": "dark"})
    response.set_header("X-Application", "example")
    response.set_cookie("theme", "dark", httponly=True, samesite="lax")
    return response
```

GET `/preferences` returns HTTP 200, JSON, the custom header and a Set-Cookie
header. Use `secure=True` for cookies served over HTTPS. The cookie in this
example stores a display preference, not an identity or a signed session.

`set_header()` replaces names case-insensitively. Use `set_cookie()` for each
cookie and `delete_cookie()` to expire it; do not manually assemble Set-Cookie
values. Keep the original path and domain when deleting a cookie. For signed
request-local Session data, see [Session middleware](middleware.md#sessions).
For a client that carries cookies between requests, see
[Cookie flows across requests](testing.md#cookie-flows-across-requests).

<span id="cookies"></span>

## Expected errors

Append this route to `app.py`:

```python
@app.get("/items/:item_id")
def get_item(ctx: Context) -> dict[str, str]:
    if ctx.path_params["item_id"] != "item-1":
        ctx.abort(404, "Item not found")
    return {"id": "item-1"}
```

GET `/items/item-1` returns HTTP 200. GET `/items/missing` returns HTTP 404
with the text `Item not found`. Use `ctx.abort()` for expected HTTP failures;
unhandled programming errors normally produce a generic 500 response and are
logged by App.

| Default error input | Response format |
|---|---|
| String detail, such as `ctx.abort(404, "Item not found")` | Text |
| JSON-compatible dictionary or list detail | JSON with `error` and `status_code` |
| Unhandled exception | Text `Internal Server Error`, status 500 |

Attrs/dataclass binding errors normally use text details; Pydantic validation
errors use structured details. The default does not force every error into
the same format.

## Use JSON for all errors

Append the following imports and handler to `app.py`. It keeps the framework's
structured errors and wraps text errors in the same JSON envelope. Status codes
and headers such as `Allow` and `WWW-Authenticate` are preserved.

```python
from lettia.errors import default_error_handler


@app.error_handler
async def json_errors(ctx: Context, exc: Exception) -> JsonResponse:
    response = await default_error_handler(ctx, exc)
    if isinstance(response, JsonResponse):
        return response
    return JsonResponse(
        {
            "error": response.body.decode("utf-8"),
            "status_code": response.status_code,
        },
        status_code=response.status_code,
        headers=response.headers.copy(),
    )
```

GET `/items/missing` now returns HTTP 404 and
`{"error":"Item not found","status_code":404}`. Unexpected exceptions still
use the generic message rather than exposing their exception details. Global
CORS and Request ID middleware can apply their policies to these responses.
Error replacement is possible only before response start has been attempted;
see the [error contract](api/app.md#error-handling) for transmission failures.

## Streaming

Append this example to `app.py`:

```python
from collections.abc import AsyncIterator

from lettia import StreamResponse


async def chunks() -> AsyncIterator[bytes]:
    yield b"first\n"
    yield b"second\n"


@app.get("/stream")
def stream(ctx: Context) -> StreamResponse:
    return StreamResponse(chunks(), media_type="text/plain; charset=utf-8")
```

GET `/stream` returns HTTP 200 with `first\nsecond\n`. Yield bytes from an async
iterable. Put cleanup for acquired resources in the generator's `finally`
block; Lettia closes the stream on completion and failure. After response start,
a stream failure cannot become a new JSON error response. HTTPX in-process
tests can buffer streams; use a real server to test disconnect cleanup.

## Post-response tasks

For short, best-effort work, register a callable with
`ctx.add_background_task()`. This fragment can be appended to `app.py`:

```python
import logging

logger = logging.getLogger(__name__)


def record_view(item_id: str) -> None:
    logger.info("Viewed item %s", item_id)


@app.get("/tracked/:item_id")
def tracked(ctx: Context) -> dict[str, str]:
    item_id = ctx.path_params["item_id"]
    ctx.add_background_task(record_view, item_id)
    return {"id": item_id}
```

The callable runs after successful transmission and stream cleanup, in the same
application call. Successfully delivered error responses can also run queued
tasks. Failed transmission, observed disconnects or failed cleanup suppress
them. This is not a durable job queue; see
[Background tasks and reliability](deployment.md#background-tasks-and-reliability).
