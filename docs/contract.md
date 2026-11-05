# Contracts

A contract is a `typing.Protocol` whose methods are decorated with `op`. It declares paths, methods, guards, request shapes and the complete set of responses of an API. An implementation class inherits the Protocol and is mounted on an `App` or a `Group`. lettia reads the Protocol once, at mount time, and compiles it into ordinary Core routes: decode the request, call the method, encode the reply. This page covers declaring, mounting, decoding and encoding. Guards and OpenAPI have their own pages.

## Overview

```python
import asyncio
from typing import Annotated, Protocol

import msgspec

from lettia import (
    App, Body, Context, Created, HeaderName, NotFound, Ok, Path, Problem, Query, op, public,
)
from lettia.testing import TestClient


class NewUser(msgspec.Struct):
    name: str


class UserOut(msgspec.Struct):
    id: int
    name: str


class GetUserReq(msgspec.Struct):
    user_id: Path[int]
    verbose: Query[bool] = False


class CreateUserReq(msgspec.Struct):
    body: Body[NewUser]


class UserCreated(Created[UserOut], kw_only=True):
    location: Annotated[str, HeaderName("Location")]


class UsersApi(Protocol):
    @op.get("/users/{user_id}", guard=public, tags=["users"])
    async def get_user(
        self, c: Context[None], req: GetUserReq
    ) -> Ok[UserOut] | NotFound[Problem]:
        """Get one user."""
        ...

    @op.post("/users", guard=public)
    async def create_user(self, c: Context[None], req: CreateUserReq) -> UserCreated: ...


class UserService(UsersApi):
    def __init__(self) -> None:
        self.users: dict[int, UserOut] = {}

    async def get_user(
        self, c: Context[None], req: GetUserReq
    ) -> Ok[UserOut] | NotFound[Problem]:
        user = self.users.get(req.user_id)
        if user is None:
            return NotFound(Problem(title="user not found"))
        return Ok(user)

    async def create_user(self, c: Context[None], req: CreateUserReq) -> UserCreated:
        user = UserOut(len(self.users) + 1, req.body.name)
        self.users[user.id] = user
        return UserCreated(user, location=f"/v1/users/{user.id}")


app = App()
app.group("/v1").mount(UserService())


async def main() -> None:
    async with TestClient(app) as http:
        created = await http.post("/v1/users", json={"name": "Ann"})
        print(created.status_code, created.headers["location"], created.json())
        print((await http.get("/v1/users/1?verbose=true")).json())
        print((await http.get("/v1/users/2")).status_code)


asyncio.run(main())
```

Output:

```
201 /v1/users/1 {'id': 1, 'name': 'Ann'}
{'id': 1, 'name': 'Ann'}
404
```

Every name above is importable from `lettia` (lazily) or from `lettia.contract`. `examples/users_api.py` is a larger version with guards.

## Declaring operations: `op`

`op.get`, `op.post`, `op.put`, `op.patch` and `op.delete` decorate an `async def` method of the Protocol. All five share one signature:

```python
op.get(
    path: str,
    *,
    guard: GuardSpec[G],
    tags: Sequence[str] = (),
    operation_id: str | None = None,
    openapi_extra: Mapping[str, object] | None = None,
)
```

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | `str` | required | Must start with `/`, otherwise `ValueError` at decoration. `{name}` placeholders are path parameters. Must not contain `*` (startup problem). |
| `guard` | `GuardSpec[G]` | required (keyword-only) | Fixes the type of `c.principal`. Use `public` for none. A non-`GuardSpec` raises `TypeError`. See [guards](guards.md). |
| `tags` | `Sequence[str]` | `()` | Stored as a tuple; used for OpenAPI grouping. |
| `operation_id` | `str \| None` | `None` | Defaults to the method name. Must be unique across everything mounted on one app. |
| `openapi_extra` | `Mapping[str, object] \| None` | `None` | Merged into the OpenAPI operation. See [openapi](openapi.md). |

`op` returns the function unchanged, with the declaration attached. The method's docstring is kept as the operation description for OpenAPI. `op.websocket` has the same options for WebSocket operations (handler `(self, c, ws: WebSocket) -> None`); see [websocket](websocket.md).

### Handler signature

Every operation method has exactly three parameters, all positional-or-keyword: `self`, the context and the request.

```python
async def name(self, c: Context[G], req: Req) -> Ok[X] | NotFound[Problem]: ...
```

- `c: Context[G]` is the Core context with a typed `principal` attribute. `G` is the type the operation's guard produces; `c.principal` is that value. For `public` operations `G` is `None` and `c.principal` is `None`; for `guard=a & b` it is a tuple (see [guards](guards.md)). The rest of the context (`c.request`, `c.path_params`, `c.state`) is the Core context; see [core](core.md).
- `req` is a `msgspec.Struct` subclass, or `None` when the operation takes no input (`req: None`, the handler then receives `None`). The parameter name is free; its annotation is required.
- The return annotation lists every response class the method may return (see Responses).

Only the Protocol is read at runtime. Annotations on the implementing methods are not inspected; they exist for the type checker. Implementation methods must be `async def`.

## Requests

A request is a `msgspec.Struct`. Each field carries exactly one source marker as its outermost type:

| Marker | Value comes from | Wire name |
| --- | --- | --- |
| `Path[T]` | `{placeholder}` in the path | field name |
| `Query[T]` | query string | field name |
| `Header[T]` | request header (case-insensitive) | field name with `_` replaced by `-`; override with `HeaderName` |
| `Cookie[T]` | request cookie | field name |
| `Body[T]` | JSON request body | the whole body |
| `Form[T]` | urlencoded or multipart text field | field name |
| `File[T]` | multipart file part | field name |

The markers are type aliases, so type checkers see `Path[int]` as plain `int`: `req.user_id` is an `int` inside the handler.

```python
class ListReq(msgspec.Struct, kw_only=True):
    limit: Query[int] = 20
    tags: Query[list[str]] = []
    after: Query[int | None] = None
    trace: Header[Annotated[str, HeaderName("X-Trace-Id")]] = ""
    accept_language: Header[str] = "en"      # reads "accept-language"
    session: Cookie[str | None] = None
```

Rules:

- **The marker is outermost.** To make a field optional, put the default on the field and put `None` inside the marker: `Query[int | None] = None`. `Query[int] | None` is a startup problem.
- **Required vs optional** follows msgspec: a field without a default is required, one with a default (or `default_factory`) is optional. Use `kw_only=True` when a required field follows a default.
- **Headers:** `HeaderName` goes inside the marker, `Header[Annotated[str, HeaderName("X-Trace-Id")]]`. The lookup is case-insensitive.
- **Body:** at most one `Body` field. It cannot be combined with `Form`/`File` fields, and `GET`/`DELETE` operations cannot have `Body`, `Form` or `File` fields.
- **Path:** the `Path` fields and the `{placeholders}` of the path must match exactly, in both directions.
- **Multi-valued fields:** a field whose type is `list`, `set`, `frozenset` or `tuple` (also inside a union) collects every value of a repeated query parameter, header or form field. Other fields use the first value.
- Unknown query parameters and unknown JSON keys are ignored.

### `req: None`

`req: None` declares that the operation reads nothing from the request. The handler is called with `req=None`.

## Decoding

Path, query, header, cookie and form values arrive as strings. They are converted leniently to the declared type, one Struct at a time: `"42"` becomes `42` for `int`, `"true"` becomes `True` for `bool`, and so on (msgspec's non-strict conversion). `limit=1.0` is accepted for an `int`; `limit=abc` and an empty `limit=` are not. A repeated parameter for a multi-valued field becomes a list: `?tags=a&tags=b` gives `["a", "b"]`. Absent values take the field default; an absent required value is an error.

The JSON body is strict: `{"qty": "3"}` for `qty: int` is rejected. Body types can be any msgspec-supported type (`Struct`, `list[Struct]`, `dict`, unions, ...).

### Body rules

- A body is read only if the request type has a `Body` field.
- If the body is empty: a field with a default uses the default; `Body[X | None]` without a default gets `None`; otherwise the request fails with a 422 (`$.body: the request body is required`).
- If the body is not empty and a `Content-Type` is present, it must be `application/json` or end in `+json`, otherwise the answer is a 415 (`expected application/json, got text/plain`). A missing `Content-Type` is accepted. Media type parameters (`; charset=utf-8`) are ignored.
- Malformed JSON is a 422 at `$.body`.
- Size limit: `App(max_body_size=...)` (default 5 MiB, `None` disables) applies to every body, JSON and form. A larger declared or streamed body is a 413 problem (`body exceeds N bytes`).

### Invalid requests: 422

Conversion failures produce `422 Unprocessable Entity` with `Content-Type: application/problem+json`. The body is a problem document with an `errors` extension; all fields of one source are reported through one entry:

```json
{
  "type": "about:blank",
  "title": "Unprocessable Entity",
  "status": 422,
  "detail": "$.query.limit: Expected `int`, got `str`",
  "errors": [
    {"in": "query", "path": "$.query.limit", "message": "Expected `int`, got `str`"}
  ]
}
```

`in` is one of `path`, `query`, `header`, `cookie`, `body`, `form`, `file`. `path` is `$.<source>` followed by the location inside the value: `$.body.name`, `$.body.items[0].id`, `$.path.user_id`. Missing required fields are located at the field itself (`$.header.token`, `$.body.name`). Decoding is one step per group: the path/query/header/cookie group, the body, the form fields, then the files; the first failing group raises. Other status codes (415, 413, 400) are Core `HTTPError` problems with `type`, `title`, `status` and `detail`.

Guards run before anything is decoded, so an unauthenticated request never reaches decoding.

## Form data and file uploads

`Form[T]` and `File[T]` fields read `application/x-www-form-urlencoded` and `multipart/form-data` bodies. They need a method that has a body (`POST`, `PUT`, `PATCH`).

```python
from lettia import File, Form, UploadFile


class UploadReq(msgspec.Struct, kw_only=True):
    title: Form[str]
    count: Form[int] = 0
    avatar: File[UploadFile]
    attachments: File[list[UploadFile]] = []
    note: File[UploadFile | None] = None
```

- Text parts and urlencoded fields feed `Form` fields and convert like query values (`count=2` becomes `2`). A `Form[list[str]]` field collects every value.
- A multipart part with a `filename` is a file; any other part is a text field. `File[UploadFile]` takes the first part with that name, `File[list[UploadFile]]` all of them.
- A missing required `File` field is a 422 with `in: "file"`; a missing optional one becomes its default (`None` for `UploadFile | None`, `[]` for lists with default `[]`). Form fields are validated before file fields.
- The form content type decides the parser: no `Content-Type` with a body is parsed as urlencoded; anything other than urlencoded or multipart is a 415. A multipart body without a boundary, or one that ends early or is malformed, is a 400.
- Uploaded files are closed automatically after the handler returns (or fails).

`UploadFile` (from `lettia` or `lettia.contract`):

| Member | Meaning |
| --- | --- |
| `filename` | Client file name or `None`. |
| `content_type` | The part's `Content-Type` or `None`. |
| `name` | Form field name. |
| `headers` | The part's headers (`Headers`). |
| `size` | Bytes written so far. |
| `rolled_to_disk` | `True` once the file spilled from memory to a temporary file. Files up to 1 MiB stay in memory. |
| `await read(size=-1)` | Read bytes from the current position. |
| `await seek(offset)` | Move the position. Uploads start at position 0. |
| `stream(chunk_size=65536, *, chunk_size_bytes=None)` | Async iterator over chunks from the start; `chunk_size_bytes` overrides `chunk_size`. |
| `await close()` | Close the file (done for you after the handler). |

## Responses

Handlers return instances of response classes, all `msgspec.Struct`s deriving from `Reply`. `Reply` has two class variables: `status: int` and `media_type: str = "application/json"`.

| Class | Status | Fields |
| --- | --- | --- |
| `Ok[T]` | 200 | `body: T` |
| `Created[T]` | 201 | `body: T` |
| `NoContent` | 204 | none |
| `NotFound[T]` | 404 | `body: T` |
| `Reply` | none | base class; cannot be returned itself |

`body` is the first positional argument: `Ok(user)`, `NotFound(Problem(title="user not found"))`. `Problem` is a plain Struct (`title: str`, `detail: str | None = None`), not a `Reply`; it is the usual `T` of `NotFound[Problem]`. It is encoded as ordinary JSON with the `application/json` content type (`{"title": "...", "detail": null}`), unlike the `application/problem+json` documents Core produces for errors; [Media type](#media-type) shows how to change that.

The return annotation is a response class or a union of them. Each status code may appear once in the union, and a bare `Ok` (without `[T]`) is a startup problem.

### Other statuses

Subclass `Reply` and set `status`:

```python
from lettia import Reply


class Accepted(Reply):
    status = 202
```

Add a `body: T` field for a body. A response class may have only the `body` field plus header fields.

### Response headers

Declare headers as keyword-only fields annotated `Annotated[str, HeaderName("Name")]`:

```python
class UserCreated(Created[UserOut], kw_only=True):
    location: Annotated[str, HeaderName("Location")]
    etag: Annotated[str | None, HeaderName("ETag")] = None
```

`UserCreated(user, location="/users/1")` sends `Location`. A field typed `... | None` is optional and is omitted when `None`; any other header field is required, and the type checker reports a missing argument. Non-`str` values are sent with `str()`. A header name that is not a valid HTTP token (for example `"Bad Name"`) is a startup problem that names the response class and field.

### Media type

`media_type` is a class variable. With a JSON media type (`application/json` or `+json`) the body is encoded as JSON. With any other media type the body must be `str` (encoded as UTF-8) or `bytes` (sent as is), otherwise a `TypeError` results (a 500). For `text/*` media types with a `str` body, `; charset=utf-8` is appended unless a charset is given.

```python
class Page(Ok[str]):
    media_type = "text/plain"      # sent as "text/plain; charset=utf-8"
```

`Problem` itself is JSON with `application/json`. To send a declared 404 as `application/problem+json`, subclass the response class and put the subclass in the return annotation:

```python
from typing import ClassVar, Protocol

from lettia import Context, NotFound, Ok, Problem, op, public


class ProblemNotFound(NotFound[Problem]):
    media_type: ClassVar[str] = "application/problem+json"


class ItemsApi(Protocol):
    @op.get("/item", guard=public)
    async def get_item(self, c: Context[None], req: None) -> Ok[str] | ProblemNotFound: ...


class Items(ItemsApi):
    async def get_item(self, c: Context[None], req: None) -> Ok[str] | ProblemNotFound:
        return ProblemNotFound(Problem(title="not found"))
```

The response is `404` with `content-type: application/problem+json` and the body `{"title":"not found","detail":null}`, and the OpenAPI 404 entry is keyed `application/problem+json` with the `Problem` schema. The encoder and the OpenAPI document both use the classes the return annotation declares. A `ProblemNotFound` returned from an operation that declares only `NotFound[Problem]` is therefore still sent as `application/json`; declare the subclass itself. The body is still a `Problem` (`title`, `detail`), not the full RFC 9457 document Core produces for errors: there is no `type` or `status` member.

### Encoding rules

- Only declared classes and their subclasses are accepted. A handler that returns anything else gets a logged `RuntimeError` and the client a 500 problem: `Api.method returned Ok, which the contract does not declare (declared: Page)`. The type checker rejects this at the override; the check is the runtime backstop.
- A required header field that is `None` is a `RuntimeError` (500).
- With `App(debug=True)` each JSON body is additionally decoded against its declared type before sending. A body that does not match, such as `Ok({"id": "x"})` declared as `Ok[Item]`, is a 500 whose detail names the mismatch. Debug mode also puts the exception text in the 500 problem's `detail`. Without debug the check is skipped and the body is sent as encoded.
- A handler may raise `HTTPError` (and `Unauthorized`, `Forbidden`) to answer with a problem document; see [core](core.md).

## Implementing and mounting

An implementation is a class that inherits the Protocol and defines each method as `async def`. Dependencies are plain attributes of the class.

```python
app = App()
app.mount(UserService())                  # at the app's root
app.group("/v1").mount(UserService())     # under a prefix
```

`mount(implementation, *, codec=None)` exists on `App` and on every `Group`, including nested groups (`app.group("/v1").group("/admin").mount(...)`). The paths of the contract are appended to the group prefix, and the group's middleware applies to the mounted routes. The same function is available as `lettia.contract.mount(target, implementation, *, codec=None)`.

- **Lookup by MRO.** Every class in the implementation's MRO that declares `op` methods is a contract of it. One object can inherit and serve several Protocols; all their operations are mounted. A method is found by normal attribute lookup, so a base class between the Protocol and the implementation may provide it.
- **The same contract cannot be mounted twice on one app**, not even under different prefixes: its `operationId`s would repeat, which is reported at startup. `operation_id=` on `op` renames an operation if you need two copies on purpose.
- Mounting never raises for contract problems; they are collected (see Startup checks). Calling `mount` or registering routes after the app was built raises `RuntimeError` (`the app is already built; register before it starts`).
- `HEAD` requests are answered for contract `GET` routes by Core.

### Codec

`App(codec=...)` and `mount(..., codec=...)` replace the model library used for decoding and encoding. The default is `MsgspecCodec`. `Codec` is a `Protocol` with `build_decoder(type_, *, json)`, `build_encoder()` and `json_schema(types, *, ref_template)`; decoders raise `CodecError(message, path)`. A codec passed to `mount` applies to that mount's decoding and encoding; the OpenAPI document uses the app's codec.

## Startup checks

All of the following are reported together as one `StartupError` (a Core exception with a `problems` list). They are not raised by `mount`; they surface when the app is built: by `app.build()`, `app.openapi()`, the ASGI lifespan startup (the server reports startup failure), `async with TestClient(app)`, or the first request when no lifespan runs. `mount` raises immediately only for a mount after build.

The message lists every problem, each prefixed by `Contract.method:` where it applies:

```
startup checks failed:
- Bad2.a: path '/a/{x}' has the placeholder '{x}' but the request has no matching Path field
- Bad2.b: Path field 'item_id' has no '{item_id}' placeholder in '/b'
- Bad2.b: status 200 is declared more than once; each status code may appear once in the response union
- Bad2.c_: GET operations cannot have a Body field
- Bad2.e: field 'a' has no source marker; mark it with Path[T], Query[T], Header[T], Cookie[T], Body[T], Form[T] or File[T]
- Bad2.f: the signature must be (self, c, req)
```

The checks:

| Area | Problem |
| --- | --- |
| Signature | not exactly three positional-or-keyword parameters (`(self, c, req)`, or `(self, c, ws)` for `op.websocket`); annotations cannot be resolved; the request parameter has no annotation |
| Path | `{placeholder}` without a `Path` field, or `Path` field without a placeholder; `*` in the path |
| Request type | not a `msgspec.Struct` or `None`; a field without a source marker (with a hint when the marker was put inside a union); a field type msgspec cannot handle |
| Body | more than one `Body`; `Body` mixed with `Form`/`File`; `Body`, `Form` or `File` on `GET` or `DELETE` |
| Responses | missing return annotation; a member that is not a `Reply` subclass (or is `Reply` itself); `Ok`/`Created`/`NotFound` without a body type; a field that is neither `body` nor an `Annotated[..., HeaderName(...)]` header; a status declared twice |
| WebSocket | return type not `None`; `*` in the path |
| Implementation | the object inherits no contract; the method is not overridden (`X does not implement Contract.method`); the method is not `async def` |
| Guards | a guard has no implementation, or is implemented twice (see [guards](guards.md)) |
| Operation ids | an `operationId` used twice on the app |
| Routes | two routes with the same method and path (`GET /ping conflicts with the registered route GET /ping`) |
| OpenAPI | problems found while deriving the document (see [openapi](openapi.md)) |

A malformed operation is reported and left out; the other operations of the contract are still checked, so one run lists everything.

## Type checking

Contracts are designed so that the type checker finds mistakes before startup. The project checks with pyrefly's `strict` preset (`[tool.pyrefly] preset = "strict"`); pyright (`strict`) and mypy (`strict`) report the same fixtures. A typical setup:

```toml
[tool.pyrefly]
python-version = "3.12"
preset = "strict"
```

Run `uv run pyrefly check`. Mistakes the checkers report, from the negative fixtures in `tests/typing/negative`:

| Mistake | pyrefly code |
| --- | --- |
| Implementation does not define every Protocol method | `bad-instantiation` (abstract member) |
| Implementation signature differs from the contract's, e.g. `Context[Admin]` where the contract has `Context[User]` | `bad-override` |
| Implementation returns a response class not declared in the contract's union | `bad-override` |
| Operation returns something that is not a `Reply` (`-> int`) | `bad-specialization` |
| `op.get(...)` without `guard=` | `missing-argument` |
| Response class constructed without a required header field | `missing-argument` |
| Contract method's `Context[Admin]` does not match the guard's `GuardSpec[User]` | `bad-argument-type` |
| Guard implementation returns another type than the spec | `bad-argument-type` |

A request type that is not a `Struct` (`req: int`) is reported by pyright and mypy but not by pyrefly; lettia reports it at startup in every case. Source markers need no special treatment because they are type aliases: `req.user_id` is `int`, `req.avatar` is `UploadFile`, `c.principal` is the guard's `G`.

## See also

- [getting-started.md](getting-started.md)
- [core.md](core.md)
- [guards.md](guards.md)
- [openapi.md](openapi.md)
- [websocket.md](websocket.md)
- [testing.md](testing.md)
- [extensions.md](extensions.md)
