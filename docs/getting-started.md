# Getting started

Build a small contract-first HTTP API with lettia: declare the contract, implement it, assemble an `App`, run it, test it, and export its OpenAPI document. The tutorial app is a per-user notes service with bearer authentication. Outputs shown below were captured from this exact code.

## Install

Python >= 3.12. The only runtime dependency is `msgspec`.

```sh
pip install lettia
# or
uv add lettia
```

Optional extras:

| Extra  | Installs      | Needed for                                        |
| ------ | ------------- | ------------------------------------------------- |
| `test` | `httpx`       | `lettia.testing.TestClient`                       |
| `yaml` | `pyyaml`      | `lettia openapi export --format yaml`             |
| `di`   | `rodi`        | `lettia.extensions.di`                            |

```sh
pip install 'lettia[test,yaml]'
```

lettia does not depend on an ASGI server. Install one yourself (`pip install uvicorn`, or granian, hypercorn).

## 1. Models, requests, responses

Plain `msgspec.Struct` types. A request type is a struct whose fields are marked with where the value comes from: `Path[...]`, `Query[...]`, `Body[...]` (also `Header`, `Cookie`, `Form`, `File`; see [contract.md](contract.md)). Responses are generic wrappers such as `Ok[T]`, `Created[T]`, `NotFound[T]`, `NoContent`.

```python
from dataclasses import dataclass
from typing import Protocol, override

import msgspec

from lettia import (
    App, Body, Context, Created, GuardSpec, HttpBearer, NotFound, Ok, Path,
    Problem, Query, RequestHead, op,
)
from lettia.core import Unauthorized


class Caller(msgspec.Struct):          # what the guard proves
    name: str


class NewNote(msgspec.Struct):
    text: str


class Note(msgspec.Struct):
    id: int
    owner: str
    text: str


class ListNotesReq(msgspec.Struct):
    limit: Query[int] = 20             # optional: has a default


class GetNoteReq(msgspec.Struct):
    note_id: Path[int]                 # matches "{note_id}" in the route


class CreateNoteReq(msgspec.Struct):
    body: Body[NewNote]
```

## 2. Declare the guard

A `GuardSpec[P]` names a guard and the type `P` of the principal it produces. It carries no implementation, so the contract can be written without secrets or I/O. `scheme=HttpBearer()` is used for OpenAPI security metadata.

```python
bearer = GuardSpec[Caller]("bearer", scheme=HttpBearer())
```

See [guards.md](guards.md).

## 3. Write the contract

A contract is a `typing.Protocol` whose methods are decorated with `op.get/post/...`. Each operation names its guard, and its signature is `(self, c: Context[P], req: RequestType) -> ResponseUnion`. The return annotation lists every response the operation may produce.

```python
class NotesApi(Protocol):
    @op.get("/notes", guard=bearer)
    async def list_notes(
        self, c: Context[Caller], req: ListNotesReq
    ) -> Ok[list[Note]]: ...

    @op.get("/notes/{note_id}", guard=bearer)
    async def get_note(
        self, c: Context[Caller], req: GetNoteReq
    ) -> Ok[Note] | NotFound[Problem]: ...

    @op.post("/notes", guard=bearer)
    async def create_note(
        self, c: Context[Caller], req: CreateNoteReq
    ) -> Created[Note]: ...
```

## 4. Implement it

Subclass the Protocol. Dependencies (a database handle, a client, a dict here) are plain fields on a frozen dataclass. `c.principal` is the `Caller` produced by the guard.

```python
@dataclass(frozen=True, slots=True)
class NoteService(NotesApi):
    notes: dict[int, Note]

    @override
    async def list_notes(self, c: Context[Caller], req: ListNotesReq) -> Ok[list[Note]]:
        mine = [n for n in self.notes.values() if n.owner == c.principal.name]
        return Ok(mine[: req.limit])

    @override
    async def get_note(
        self, c: Context[Caller], req: GetNoteReq
    ) -> Ok[Note] | NotFound[Problem]:
        note = self.notes.get(req.note_id)
        if note is None or note.owner != c.principal.name:
            return NotFound(Problem(title="note not found"))
        return Ok(note)

    @override
    async def create_note(
        self, c: Context[Caller], req: CreateNoteReq
    ) -> Created[Note]:
        note = Note(max(self.notes, default=0) + 1, c.principal.name, req.body.text)
        self.notes[note.id] = note
        return Created(note)
```

Do not store per-request state on `self`. One instance serves all concurrent requests; per-request data belongs in `c` and `req`. `frozen=True` makes assigning to `self` an error; the dict above is shared application state, not request state.

## 5. Implement the guard

A guard implementation is an async function taking a `RequestHead` and returning the principal, or raising an HTTP error. It lives next to your secrets, not in the contract.

```python
TOKENS = {"ann-token": "ann", "bob-token": "bob"}


async def verify_bearer(head: RequestHead) -> Caller:
    scheme, _, token = head.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or token not in TOKENS:
        raise Unauthorized("a valid bearer token is required")
    return Caller(TOKENS[token])
```

## 6. Assemble the app

Bind implementations to guard declarations with `guards=[spec.use(fn)]`, then mount the service. `app.group(prefix).mount(...)` prefixes every route of the contract; `app.mount(...)` mounts at the root.

```python
app = App(title="Notes API", version="0.1.0", guards=[bearer.use(verify_bearer)])
app.group("/v1").mount(NoteService({1: Note(1, "ann", "hello")}))
```

Put steps 1-6 in `notes_app.py`. If a contract operation uses a guard that has no implementation, the app refuses to start; see [contract.md](contract.md#startup-checks).

## 7. Run it

```sh
pip install uvicorn
uvicorn notes_app:app
```

```sh
curl -i -H "Authorization: Bearer ann-token" localhost:8000/v1/notes/1
```

```
HTTP/1.1 200 OK
content-type: application/json

{"id":1,"owner":"ann","text":"hello"}
```

`/openapi.json` is served by default. `App(docs_url="/docs")` adds an interactive viewer; see [openapi.md](openapi.md).

## 8. What callers see

| Request                                              | Status | Content-Type               |
| ---------------------------------------------------- | ------ | -------------------------- |
| `GET /v1/notes` with `Bearer ann-token`              | 200    | `application/json`         |
| `GET /v1/notes` without credentials                  | 401    | `application/problem+json` |
| `GET /v1/notes?limit=abc`                            | 422    | `application/problem+json` |
| `GET /v1/notes/99`                                   | 404    | `application/json`         |
| `POST /v1/notes` with `{"text": "x"}`                | 201    | `application/json`         |
| `POST /v1/notes` with `{"txt": "x"}`                 | 422    | `application/problem+json` |

401 (`HTTPError` raised by the guard):

```json
{"type":"about:blank","title":"Unauthorized","status":401,"detail":"a valid bearer token is required"}
```

422 (request decoding failed; `errors` locates each problem):

```json
{"type":"about:blank","title":"Unprocessable Entity","status":422,"detail":"$.query.limit: Expected `int`, got `str`","errors":[{"in":"query","path":"$.query.limit","message":"Expected `int`, got `str`"}]}
```

The 404 is a response the contract declared (`NotFound[Problem]`), so it is encoded as the body you returned:

```json
{"title":"note not found","detail":null}
```

## 9. Add a plain core route

`App` extends the core `App`, so ordinary handlers work alongside contracts. A core handler takes a `lettia.core.Context` and returns a `Response`; no contract is involved.

```python
from lettia.core import Context as CoreContext, Response


async def ping(c: CoreContext) -> Response:
    return Response(200, b"pong")


app.get("/ping", ping)
```

`GET /ping` returns `200` with body `pong`. See [core.md](core.md).

## 10. Test it

`TestClient` is an `httpx.AsyncClient` that talks to the app in memory. Entering `async with` builds the app (startup problems raise here) and runs lifespan hooks. `make_context(principal)` builds a `Context` so you can call a method of the service directly, without HTTP.

```python
import asyncio

from lettia.testing import TestClient, make_context
from notes_app import app, Caller, ListNotesReq, Note, NoteService

AUTH = {"Authorization": "Bearer ann-token"}


async def main() -> None:
    async with TestClient(app) as client:
        r = await client.get("/v1/notes", headers=AUTH)
        assert r.status_code == 200
        assert r.json() == [{"id": 1, "owner": "ann", "text": "hello"}]

        assert (await client.get("/v1/notes")).status_code == 401
        assert (await client.get("/ping")).text == "pong"

    service = NoteService({1: Note(1, "ann", "hi")})
    result = await service.list_notes(make_context(Caller("ann")), ListNotesReq())
    assert [n.text for n in result.body] == ["hi"]


asyncio.run(main())
```

Use whichever async test runner you prefer (the project's own tests use `pytest-asyncio` with `asyncio_mode = "auto"`). See [testing.md](testing.md).

## 11. Export OpenAPI

The OpenAPI 3.1 document is derived from the contract.

```sh
lettia openapi export notes_app:app                          # JSON to stdout
lettia openapi export notes_app:app --output openapi.json
lettia openapi export notes_app:app --format yaml            # needs lettia[yaml]
```

The argument is `module:attribute`, resolved with the current directory on `sys.path`. Exit code is 0 on success, 1 if the app fails its startup checks (problems are printed to stderr), 2 for a bad reference or import. The same document is available in code as `app.openapi()`. See [openapi.md](openapi.md).

## 12. Strict type checking

The contract is checked by the type checker, not at runtime only. The repository's own configuration uses pyrefly in strict mode:

```toml
[tool.pyrefly]
preset = "strict"
```

pyright (`typeCheckingMode = "strict"`) and mypy (`strict = true`) are also configured in the repository; the fixtures under `tests/typing/negative` are checked against all three. Two mistakes pyrefly reports, from those fixtures:

An implementation that omits a contract method (`neg_missing_method.py`):

```python
class Api(Protocol):
    @op.get("/a", guard=bearer)
    async def a(self, c: Context[User], req: Req) -> NoContent: ...

    @op.get("/b", guard=bearer)
    async def b(self, c: Context[User], req: Req) -> NoContent: ...


class Impl(Api):
    async def a(self, c: Context[User], req: Req) -> NoContent:
        return NoContent()


Impl()
```

```
ERROR Cannot instantiate `Impl` because the following members are abstract: `b` [bad-instantiation]
```

A guard implementation returning a different principal type than the `GuardSpec` declares (`neg_guard_impl_wrong_type.py`):

```python
bearer = GuardSpec[User]("bearer")


async def lookup(head: object) -> ApiKey:
    return ApiKey()


bearer.use(lookup)
```

```
ERROR Argument `(head: object) -> Coroutine[Unknown, Unknown, ApiKey]` is not assignable to parameter `fn` with type `(RequestHead) -> Awaitable[User]` in function `lettia.contract.guards.GuardSpec.use` [bad-argument-type]
```

Other negative fixtures cover a request type that is not a struct, a signature that differs from the contract, an undeclared response, a missing response header, a non-response return type, an operation without a guard, and a guard/context mismatch.

## See also

- [index.md](index.md): docs overview and layer map
- [contract.md](contract.md): operations, requests, responses, mounting, startup checks
- [guards.md](guards.md): declaring, combining and implementing guards
- [core.md](core.md): the core layer without a contract
- [openapi.md](openapi.md): derivation, export CLI, docs UI
- [testing.md](testing.md): `make_context` and `TestClient`
- [websocket.md](websocket.md): WebSockets
- [extensions.md](extensions.md): di, tasks, staticfiles
