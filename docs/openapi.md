# OpenAPI

lettia derives an OpenAPI 3.1 document from the mounted contracts when the app is built. Nothing is written by hand: paths, parameters, request bodies, responses, security and schemas all come from the `op` declarations, the request and response classes, the guards and the handler docstrings. The document is served by the app, available as a Python dict through `app.openapi()`, and can be exported to a file with the `lettia` command. An optional interactive viewer (Scalar or Swagger UI) can be served too.

Request and response class definitions are covered in [contract.md](contract.md); this page describes how they appear in the document.

## A worked example

```python
from typing import Annotated, Protocol

import msgspec

from lettia import (
    ApiKeyHeader, App, Body, Context, Created, GuardSpec, Header, HeaderName,
    HttpBearer, NoContent, NotFound, Ok, Path, Problem, Query, RequestHead,
    op, public,
)
from lettia.core import Unauthorized


class User(msgspec.Struct):
    name: str

class NewItem(msgspec.Struct):
    title: str
    price: float = 0.0

class Item(msgspec.Struct):
    id: int
    title: str

class GetItemReq(msgspec.Struct):
    item_id: Path[int]
    verbose: Query[bool] = False
    trace: Header[Annotated[str, HeaderName("X-Trace-Id")]] = ""

class CreateItemReq(msgspec.Struct):
    body: Body[NewItem]


bearer = GuardSpec[User]("bearer", scheme=HttpBearer(format="JWT"))
apikey = GuardSpec[User]("apikey", scheme=ApiKeyHeader("X-API-Key"))


class ShopApi(Protocol):
    @op.get("/items/{item_id}", guard=bearer | apikey, tags=["items"],
            openapi_extra={"deprecated": True})
    async def get_item(
        self, c: Context[User], req: GetItemReq
    ) -> Ok[Item] | NotFound[Problem]:
        """Get one item.

        Looks the item up by id.
        """
        ...

    @op.post("/items", guard=bearer, operation_id="createItem")
    async def create_item(
        self, c: Context[User], req: CreateItemReq
    ) -> Created[Item]:
        """Create an item."""
        ...

    @op.get("/ping", guard=public)
    async def ping(self, c: Context[None], req: None) -> NoContent: ...


class Shop(ShopApi):
    async def get_item(self, c, req): return Ok(Item(req.item_id, "x"))
    async def create_item(self, c, req): return Created(Item(1, req.body.title))
    async def ping(self, c, req): return NoContent()


async def verify(head: RequestHead) -> User:
    raise Unauthorized()


app = App(
    title="Shop", version="1.0.0", description="Demo shop", docs_url="/docs",
    guards=[bearer.use(verify), apikey.use(verify)],
)
app.mount(Shop())

doc = app.openapi()
```

Pieces of `doc` for this app (all values below were produced by the code above):

```python
doc["info"]
# {'title': 'Shop', 'version': '1.0.0', 'description': 'Demo shop'}

list(doc["paths"])
# ['/items', '/items/{item_id}', '/ping']

doc["paths"]["/items/{item_id}"]["get"]["security"]
# [{'bearer': []}, {'apikey': []}]

list(doc["paths"]["/items/{item_id}"]["get"]["responses"])
# ['200', '401', '403', '404', '422']

doc["paths"]["/ping"]["get"]
# {'operationId': 'ping', 'responses': {'204': {'description': 'No Content'}}, 'security': []}

doc["components"]["securitySchemes"]
# {'apikey': {'type': 'apiKey', 'in': 'header', 'name': 'X-API-Key'},
#  'bearer': {'type': 'http', 'scheme': 'bearer', 'bearerFormat': 'JWT'}}

list(doc["components"]["schemas"])
# ['Item', 'NewItem', 'Problem', 'ProblemDetails', 'ValidationIssue', 'ValidationProblem']
```

The `GET /items/{item_id}` operation, in full:

```json
{
  "tags": ["items"],
  "summary": "Get one item.",
  "description": "Looks the item up by id.",
  "operationId": "get_item",
  "parameters": [
    {"name": "item_id", "in": "path", "required": true, "schema": {"type": "integer"}},
    {"name": "verbose", "in": "query", "required": false, "schema": {"type": "boolean", "default": false}},
    {"name": "X-Trace-Id", "in": "header", "required": false, "schema": {"type": "string", "default": ""}}
  ],
  "responses": {
    "200": {"description": "OK", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Item"}}}},
    "401": {"description": "Unauthorized", "content": {"application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}}},
    "403": {"description": "Forbidden", "content": {"application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}}},
    "404": {"description": "Not Found", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Problem"}}}},
    "422": {"description": "Unprocessable Entity", "content": {"application/problem+json": {"schema": {"$ref": "#/components/schemas/ValidationProblem"}}}}
  },
  "security": [{"bearer": []}, {"apikey": []}],
  "deprecated": true
}
```

(Whitespace condensed; the key order is the one the generator emits.)

## What is derived from what

| Document part | Source |
|---|---|
| `openapi` | Always `"3.1.0"` |
| `info.title`, `info.version`, `info.description` | `App(title=, version=, description=)`; defaults `"API"`, `"0.0.0"`; `description` is omitted when `None` |
| `paths` key | Group prefix(es) plus the `op` path, for example `app.group("/v1").mount(...)` with `/users` gives `/v1/users` |
| Method key | The `op` verb in lower case |
| `operationId` | `op(..., operation_id=...)`, otherwise the contract method name. Must be unique across the app; a repeat is a startup problem (`operationId 'x' of A.b is already used by C.d`) |
| `tags` | `op(..., tags=[...])`; omitted when empty |
| `summary`, `description` | The method docstring in the contract (dedented). First line is the `summary`; the rest, stripped, is the `description`. A one-line docstring gives only `summary`; no docstring gives neither |
| `parameters` | Path, query, header and cookie fields of the request class |
| `requestBody` | The `Body`, `Form` and `File` fields of the request class |
| `responses` | The reply classes in the return annotation, plus automatic 401/403/422 |
| `security` | The operation's guard |
| `components.schemas` | Every struct referenced above, plus the problem schemas when used |
| `components.securitySchemes` | Guards with a `scheme` that appear on at least one documented operation |

`components` is omitted if it would be empty. WebSocket operations are not part of the document. The OpenAPI endpoint and the docs page are not documented operations either.

### Ordering and determinism

Output does not depend on the order contracts were mounted or on dict iteration:

- `paths` are sorted by path string.
- Within a path the methods come in the order `get`, `put`, `post`, `delete`, `patch`; any other method follows.
- `parameters` are sorted by location (`path`, `query`, `header`, `cookie`) and then by wire name (the name on the wire, for example the `HeaderName` value).
- `responses` are sorted by numeric status, keys as strings.
- Response `headers`, form `properties` and form `required` entries are sorted by name.
- `components.schemas` and `components.securitySchemes` are sorted by name.
- Status descriptions come from a fixed table in lettia, so they do not vary with the Python version. Statuses outside the table (for example 207) get the description `"Response"`. The table: 200 OK, 201 Created, 202 Accepted, 204 No Content, 301 Moved Permanently, 302 Found, 304 Not Modified, 400 Bad Request, 401 Unauthorized, 403 Forbidden, 404 Not Found, 405 Method Not Allowed, 409 Conflict, 410 Gone, 412 Precondition Failed, 413 Payload Too Large, 415 Unsupported Media Type, 422 Unprocessable Entity, 429 Too Many Requests, 500 Internal Server Error, 502 Bad Gateway, 503 Service Unavailable.

Building twice gives identical bytes, which is what makes a committed snapshot diffable (see [CI snapshot](#ci-snapshot)).

### Parameters

Every path, query, header and cookie field becomes a parameter with `name` (wire name), `in`, `required` and `schema`.

- Path parameters are always `required: true`.
- Other parameters are `required` when the field has no default.
- A field default is added to the parameter schema as `default` (for example `{"type": "integer", "default": 10}`), when it can be JSON-encoded.
- List-typed fields become array schemas.
- An operation with no parameters has no `parameters` key.

### Request body

| Request fields | `requestBody` |
|---|---|
| A `Body[T]` field | `content["application/json"].schema` is the schema of `T`. `required` is `true` unless the body is optional or `T` accepts `None` |
| `Form` fields, no `File` | `application/x-www-form-urlencoded`, object schema with sorted `properties`; `required` lists required fields (sorted) and the body is required if any field is |
| Any `File` field | `multipart/form-data`; a file is `{"type": "string", "format": "binary"}`, a multi-file field is an array of those |
| None of these | No `requestBody` (also for `GET` operations that only have parameters) |

### Responses

Each reply class in the return annotation contributes one response keyed by its status.

- `description` is the table wording above.
- A reply without a body has no `content`.
- A reply body is keyed by its `media_type` without parameters (`text/plain; charset=utf-8` becomes `text/plain`). JSON media types (`application/json`, `*+json`) reference the body schema. For other media types, a `bytes` body is `{"type": "string", "format": "binary"}` and a `str` body is `{"type": "string"}`.
- Declared reply headers are listed under `headers` with `required` and `schema`.

Automatic responses are added with `setdefault`, so a status you declare in the return annotation is never replaced:

| Status | Added when | Schema (`application/problem+json`) |
|---|---|---|
| 401 and 403 | The guard can refuse. `public` cannot. For `a \| b` it can refuse only if *every* part can; for `a & b` if *any* part can. So `bearer \| public` gets neither, `bearer & admin` gets both | `ProblemDetails` |
| 422 | The request class has at least one field (parameters, body, form or file) | `ValidationProblem` |

The problem schemas are generated from these structs and are added to `components.schemas` only when referenced:

| Schema | Fields |
|---|---|
| `ProblemDetails` | `type` (default `"about:blank"`), `title`, `status`, `detail` (optional) |
| `ValidationProblem` | the above plus `errors`: list of `ValidationIssue` (default `[]`) |
| `ValidationIssue` | `in` (location), `path`, `message` |

Declared problem replies such as `NotFound[Problem]` use their own class and media type, as in the 404 entry above.

### Security

`guard` is turned into `security` requirement objects; each scheme name is the guard name.

| Guard | `security` |
|---|---|
| `public` | `[]` (explicitly no security) |
| `bearer` | `[{"bearer": []}]` |
| `bearer \| apikey` | `[{"bearer": []}, {"apikey": []}]` (alternatives) |
| `bearer & apikey` | `[{"bearer": [], "apikey": []}]` (both required, one object) |
| `bearer \| public` | `[{"bearer": []}, {}]` (anonymous allowed) |
| A guard without `scheme` | contributes an empty requirement `{}`; if that is the only requirement the `security` key is omitted (`bearer & admin` where `admin` has no scheme gives `[{"bearer": []}]`) |

Duplicate requirement objects are removed. `components.securitySchemes` defines each guard that has a scheme, keyed by guard name. Two different guards with the same name but different scheme definitions are a startup problem: `security scheme 'x' is declared by guards with different definitions`. See [guards.md](guards.md).

### `openapi_extra`

`op(..., openapi_extra={...})` merges extra keys into the operation object (for example `deprecated`, `x-internal`, `externalDocs`). Extra keys are applied after the derived ones, so they overwrite `summary`, `description`, `tags`, `operationId` and `security` if you set them. The derived parts `parameters`, `requestBody` and `responses` cannot be set: that is a startup problem (`Api.a: openapi_extra may not set 'parameters'`).

### Schema names

Struct schemas are named after the class and referenced as `#/components/schemas/<Name>`. When two different types share a name, the schema generator qualifies them with their module (for example `shop.Item` and `__main__.Item`). If names still collide, building the document fails with a startup problem such as `OpenAPI schemas: another schema is also named 'Item'; rename one of the types`. A type msgspec has no JSON Schema for (for example `dict[str, object]`; use `dict[str, Any]`) is likewise a startup problem, `OpenAPI schemas: cannot generate a JSON schema: ...`, not a crash.

## App options

```python
App(
    title="API",
    version="0.0.0",
    description=None,
    openapi_url="/openapi.json",
    docs_url=None,
    docs_ui="scalar",
    ...
)
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `title` | `str` | `"API"` | `info.title`; also the HTML title of the docs page |
| `version` | `str` | `"0.0.0"` | `info.version` |
| `description` | `str \| None` | `None` | `info.description` |
| `openapi_url` | `str \| None` | `"/openapi.json"` | Path where the document is served as `application/json`; `None` disables the endpoint (the document is still derived, `app.openapi()` and the CLI keep working) |
| `docs_url` | `str \| None` | `None` | Path of the interactive viewer; `None` disables it |
| `docs_ui` | `"scalar" \| "swagger"` | `"scalar"` | Which viewer `docs_url` serves |

Constructor errors: `docs_ui` other than `"scalar"`/`"swagger"` raises `ValueError`; `docs_url` with `openapi_url=None` raises `ValueError` (the viewer needs the document).

The endpoint and the docs page are ordinary `GET` routes: app middleware applies to them, `HEAD` is answered, and defining your own route on the same path is a startup conflict.

### `app.openapi()`

```python
def openapi(self) -> dict[str, Any]
```

Returns the document as plain data (dicts, lists, strings, numbers). It builds the app first, so startup problems raise `StartupError`, and after the call you can no longer mount contracts (`RuntimeError`). Each call returns a fresh copy; changing it does not affect the served document.

Startup problems from document derivation (bad `openapi_extra`, scheme clashes, schema name clashes, missing guard implementations, duplicate `operationId`) are reported by `StartupError` together with every other startup problem.

## Exporting: `lettia openapi export`

```
lettia openapi export APP [--format {json,yaml}] [--output FILE]
python -m lettia openapi export APP ...
```

| Argument | Meaning |
|---|---|
| `APP` | `module:attribute` of an `App` instance. The attribute may be dotted (`pkg.mod:holder.app`). The current directory is put on `sys.path` first, so a module next to where you run the command is importable |
| `--format` | `json` (default; 2-space indent, non-ASCII kept, trailing newline) or `yaml` |
| `--output FILE` | Write to `FILE` (UTF-8, `\n` line endings on every OS) instead of stdout. The parent directory must exist |

```
lettia openapi export shop:app
lettia openapi export shop:app --format yaml --output openapi.yaml
python -m lettia openapi export shop:app --output openapi.json
```

YAML output needs PyYAML: `pip install 'lettia[yaml]'`. Without it the command exits with `lettia: error: YAML output needs PyYAML: pip install 'lettia[yaml]'`.

Only the OpenAPI document is exported; the app does not start (no lifespan), but the module is imported and the app is built.

### Exit codes

| Code | When | Message (stderr) |
|---|---|---|
| 0 | Success | |
| 1 | `StartupError` while building | `lettia: the application cannot start:` followed by one `  - <problem>` line per problem |
| 2 | Bad reference: not `module:attribute`, import failure, missing attribute, or the attribute is not a `lettia.App`. Also missing PyYAML for `--format yaml` | `lettia: error: ...` |
| 2 | Invalid options (for example `--format xml`) or no subcommand | argparse usage message |

Examples of the `exit 2` messages:

```
lettia: error: 'nocolon' is not of the form module:attribute
lettia: error: cannot import 'missing': ModuleNotFoundError: No module named 'missing'
lettia: error: module 'shop' has no attribute 'nope'
lettia: error: 'shop:ShopApi' is not a lettia.App
```

### CI snapshot

Commit the exported document and fail CI when it drifts from the code. The repository does this for its example (`examples/openapi.json`):

```
uv run lettia openapi export examples.users_api:app --output examples/openapi.json
git diff --exit-code examples/openapi.json
```

A contract change then shows up as a reviewable diff in the snapshot. Because output is deterministic and line endings are always `\n`, the snapshot is stable across machines and operating systems.

## Interactive docs

```python
app = App(title="Shop", docs_url="/docs")                       # Scalar
app = App(title="Shop", docs_url="/docs", docs_ui="swagger")    # Swagger UI
```

`GET /docs` returns an HTML page (`text/html; charset=utf-8`) that loads the viewer from the jsDelivr CDN and points it at `openapi_url` (a custom `openapi_url` is followed automatically). The page title is the app `title`; both the title and the URL are escaped so they cannot break out of the page.

The viewer is fetched by the browser, not by the server, so the browser needs internet access to `cdn.jsdelivr.net`.

### Pinned versions and SRI

Every CDN file is pinned to an exact version and carries a Subresource Integrity hash with `crossorigin="anonymous"`, so a tampered CDN file is not executed. The pins live in `lettia/docs.py`:

| Viewer | Constant | Files |
|---|---|---|
| Swagger UI | `SWAGGER_UI_VERSION` (`swagger-ui-dist`) | `swagger-ui.css`, `swagger-ui-bundle.js` |
| Scalar | `SCALAR_VERSION` (`@scalar/api-reference`) | `dist/browser/standalone.js` |

To bump a version, change the version constant and recompute the SHA-384 hash of every file of that viewer, then replace the matching `sha384-...` value:

```
curl -sL https://cdn.jsdelivr.net/npm/swagger-ui-dist@<version>/swagger-ui-bundle.js \
  | openssl dgst -sha384 -binary | openssl base64 -A
```

Prefix the result with `sha384-`. A wrong hash makes the browser refuse to run the file. The unit tests check that every external `<script>`/`<link>` is version-pinned, has an `integrity` attribute and `crossorigin="anonymous"`.

### Errors

| Condition | Result |
|---|---|
| `docs_ui` not `"scalar"` or `"swagger"` | `ValueError` from `App(...)` |
| `docs_url` set while `openapi_url=None` | `ValueError` from `App(...)` |
| A route already registered on `docs_url` or `openapi_url` | Startup conflict reported by `app.build()` |

## See also

- [contract.md](contract.md): `op`, request and response classes
- [guards.md](guards.md): guards, schemes and failure semantics
- [getting-started.md](getting-started.md): first app, running and exporting
- [testing.md](testing.md): `TestClient`
