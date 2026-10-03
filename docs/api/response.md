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

`set_header()` replaces all case variants of the name with one lowercase key,
including headers supplied at construction or through a tuple response.
Names must be nonempty ASCII HTTP tokens. Values must be Latin-1 encodable;
control characters other than horizontal tabs are rejected, including CR/LF,
NUL, and DEL. Invalid input raises `ValueError` before modifying the headers. The public
`headers` mapping remains a `dict[str, str]`. Direct dictionary writes are
validated by the writer before any response event is sent; duplicate
Content-Length fields are rejected.

Explicit Content-Length values must contain only ASCII digits. Leading zeros
are accepted and preserved. For a complete non-streaming body, the numeric
length must match its byte count. HEAD may instead specify the length of the
corresponding GET representation with an empty body; the writer validates the
header syntax without requiring that representation to be constructed. With
no explicit length, it uses the supplied body's byte count. Responses with
status 204 or 304 still omit Content-Length and body bytes.

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
    *,
    secure: bool = False,
    httponly: bool = False,
    samesite: str = "lax",
) -> None
```

Multiple cookies are stored and emitted as repeated `set-cookie` ASGI headers.
All string cookie attributes, including `expires`, are validated before mutation.
Names must be ASCII tokens; values must use HTTP cookie-octet characters
(ASCII without whitespace, double quotes, commas, semicolons, or backslashes).
Encode arbitrary text into a cookie-safe format before calling `set_cookie()`;
Lettia preserves the supplied value without implicit quoting or decoding.
Attributes must be printable ASCII without semicolons; SameSite accepts
`lax`, `strict`, `none` (case-insensitive), or an empty string to omit it.
Invalid input raises `ValueError` atomically. Appending or deleting a cookie
preserves existing Set-Cookie values and their order, regardless of header-name
case.

`delete_cookie()` retains its three positional arguments and accepts optional
security attributes by keyword. Supply the same Path, Domain, Secure, HttpOnly,
and SameSite settings used to create the cookie, especially `samesite="none"`
for cross-site use. Deletion automatically enables Secure for `__Host-` and
`__Secure-` names (case-insensitive). Creation requires Secure for these prefixes;
both creation and deletion reject a `__Host-` cookie with a non-root Path or Domain.

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

The writer acquires, iterates, and closes each stream in one task and one
Context. Context variables set during iteration can therefore be reset during
cleanup, including after a failed send. This task inherits the caller's context;
changes made inside the stream do not propagate back to the caller. Cancellation
protection covers the stream task, so cleanup is not moved into another Context.

Within App, the writer monitors client disconnects through a Context-owned
callback while streaming, including while the generator
is waiting for new data. A disconnect cancels response writing, closes the
iterator, and skips background tasks. On normal completion the monitor is
cancelled and awaited. A disconnect reported after the final body send does
not cancel iterator cleanup: the writer waits for cleanup before App runs background
tasks. Cleanup failures still suppress background tasks. Response deadlines
apply to sending, not the final iterator close. HEAD, 204, and 304 do not start
a disconnect monitor.
If a deadline is already cancelling a generator inside `anext()`, a subsequent
disconnect does not cancel its asynchronous `finally` again. The writer also waits for
cleanup through repeated external cancellation, then propagates cancellation.
No background tasks run on these interrupted requests. Successfully delivered
error/fallback responses use the same background-task completion path as normal
responses; a failed send or failed cleanup never becomes successful by retrying.
The monitor shares the request-body cache with `ctx.body()`; use that API for
lazy reads inside a generator, rather than reading `ctx.receive` concurrently.
Unread request data is buffered for these reads. Apply `body_limit()` before
returning a stream when requests need a size limit.
Rejected request bodies are never cached by the monitor; subsequent ASGI body
events are discarded without accumulation while waiting for disconnect.

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

Direct `write()` calls remain supported, serialized and return `None`; they do
not start a receive/disconnect monitor. App supplies a Context-owned disconnect
callback through a private delivery interface and uses its completion result.
The writer owns coordination and awaits stream cleanup before returning; App
does not access the writer's transport-state flags.

The writer:

- emits `http.response.start` once;
- adds a default `content-type` when one is not supplied;
- adds `content-length` for non-streaming bodies, except 204 and 304;
- validates explicit Content-Length as nonempty ASCII digits for both regular
  and streaming responses before attempting response start;
- emits multiple `set-cookie` header lines;
- streams async body chunks with `more_body=True`; and
- suppresses body bytes for `HEAD` while retaining representation headers; and
- suppresses body bytes and Content-Length for 204/304, including explicit headers.

After response start successfully returns from `send()`, `committed` becomes
true. Once start has been attempted, repeated writes are ignored even if the
transport failed or was cancelled before returning. Only failures before that
attempt, such as invalid headers, can be replaced by an error response. App's
error and fallback responses use the same writer and obey HEAD semantics.
App responses apply registered CORS, Request ID, and Session policies at this
boundary, including middleware errors and replacement responses. Session reads
the latest state after error handling. Each attempt uses a fresh header copy,
so reusing a response cannot accumulate policy cookies. Route handlers, rate
limiting, and other request-side work are not rerun. Arbitrary application
headers from an invalid response are not copied to a new error response.
Async iterators are closed on completion, source errors, transport
errors, and cancellation. A source error terminates the body once and propagates;
a send failure propagates without attempting another write. A framework deadline
uses event-loop time; unrelated upstream `TimeoutError` is never converted into
a framework timeout.
