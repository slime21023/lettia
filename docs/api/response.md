---
title: Response API
---

# Responses API

Lettia separates response representation from ASGI message emission. Handlers
can return simple Python values; `normalize_response()` turns them into a
`Response`, and `ResponseWriter` emits the ASGI messages.

## `Response`

```python
Response(
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    media_type: str = "text/plain; charset=utf-8",
    async_body: AsyncIterable[bytes] | None = None,
)
```

The response stores a complete byte body or an async byte stream. Do not use
both as the primary body source; `async_body` takes precedence during writing.

### Headers

```python
response.set_header(name: str, value: str) -> None
```

Header names are normalized to lowercase. Names and values containing CR or LF
raise `ValueError` to prevent header injection.

### Cookies

```python
response.set_cookie(
    key: str,
    value: str,
    max_age: int | None = None,
    path: str = "/",
    domain: str | None = None,
    secure: bool = False,
    httponly: bool = False,
    samesite: str = "lax",
    expires: str | None = None,
) -> None

response.delete_cookie(
    key: str,
    path: str = "/",
    domain: str | None = None,
) -> None
```

Multiple cookies are stored and emitted as repeated `set-cookie` ASGI headers.
Cookie attributes are checked for CR/LF characters.

## Concrete response types

### `TextResponse`

```python
TextResponse(
    text: str,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    media_type: str = "text/plain; charset=utf-8",
)
```

### `JsonResponse`

```python
JsonResponse(
    data: JSONValue,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    media_type: str = "application/json",
)
```

JSON is serialized with UTF-8 and `ensure_ascii=False`.

### `StreamResponse`

```python
StreamResponse(
    generator: AsyncIterable[bytes],
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    media_type: str = "application/octet-stream",
)
```

The generator is consumed only when the response is written. Set
`content-length` explicitly when the stream size is known.

## Return-value normalization

```python
normalize_response(result: ResponseValue) -> Response
```

The conversion rules are:

| Handler result | Response |
|---|---|
| `Response` | Returned unchanged |
| `str` | `TextResponse` |
| `bytes` | Binary `Response` |
| `dict` or `list` | `JsonResponse` |
| `(body, status_code)` | Body normalized, status replaced |
| `(body, status_code, headers)` | Body normalized, status and headers applied |
| Other values | `TypeError` |

## `ResponseWriter`

```python
ResponseWriter(send: HTTPSend, head_only: bool = False)
await writer.write(response: Response, deadline: float | None = None) -> None
```

The writer:

- emits `http.response.start` once;
- adds a default `content-type` when one is not supplied;
- adds `content-length` for non-streaming bodies, except 204 and 304;
- emits multiple `set-cookie` header lines;
- streams async body chunks with `more_body=True`; and
- suppresses body bytes for `HEAD` while retaining representation headers; and
- suppresses body bytes and Content-Length for 204/304, including explicit headers.

After response start is sent, `committed` becomes true and repeated writes are
ignored. Async iterators are closed on completion, source errors, transport
errors, and cancellation. A source error terminates the body once and propagates;
a send failure propagates without attempting another write. A framework deadline
uses event-loop time; unrelated upstream `TimeoutError` is never converted into
a framework timeout.
