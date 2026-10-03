---
title: Protocols and Extensions API
---

# Protocols and Extensions API

Lettia keeps integration points outside the application core. The protocols
describe small contracts that application code can implement or replace.

## Binder protocol

```python
class Binder(Protocol):
    async def bind(self, ctx: Context, target_type: type[T]) -> T: ...
```

Built-in implementations are:

| Binder | Target |
|---|---|
| `AttrsBinder` | `attrs` classes |
| `DataclassBinder` | standard-library dataclasses |
| `PydanticBinder` | Pydantic v2 models and types |

Each binder reads request data from `Context`, performs the model-specific
construction, and converts invalid input into HTTP 400 where appropriate.
Attrs/dataclass binders accept only scalar `str`/`int`/`float`/`bool` annotations
and nullable variants. Null preserves `None` only for nullable fields; strings
are checked strictly. Unsupported schema annotations raise `TypeError` rather
than HTTP 400. JSON fields override query parameters and omitted fields retain
defaults. Use PydanticBinder for nested or collection models.
Only `init=True` fields are bound and schema-checked. Attrs uses constructor
aliases (including the implicit `_name` to `name` alias); dataclasses use field
names. Dataclass `InitVar[T]` constructor parameters also participate, using the
scalar or nullable annotation `T`; inherited parameters, keyword-only
parameters, and defaults are preserved. Integer-to-float overflow and JSON
decoding/validation depth overflow return 400. These rules apply both to direct
binder calls and `ctx.bind()`.
Pydantic is imported lazily; applications that use it must install the
`pydantic` extra.

The request adapter reads a JSON object only for POST, PUT and PATCH, then fills
missing keys from the first value of each query parameter. Pure model analysis
and scalar conversion live below that adapter, without request I/O. Binding does
not include path parameters or form fields.

| Failure | Observable result |
|---|---|
| Unsupported attrs/dataclass constructor annotation | Configuration `TypeError` before body binding |
| Attrs/dataclass scalar conversion or constructor `TypeError` / `ValueError` | HTTP 400 |
| Pydantic `ValidationError` | HTTP 400 with Pydantic error details |
| Pydantic validator `TypeError` / `ImportError` | Original exception propagates; App normally renders HTTP 500 |
| Direct PydanticBinder call without Pydantic installed | Configuration `RuntimeError` |

Direct `PydanticBinder` also supports types via Pydantic's TypeAdapter;
`ctx.bind()` automatically selects Pydantic only for BaseModel subclasses.
Unknown-field handling and coercion for Pydantic targets follow that model's
configuration, not the attrs/dataclass scalar rules.

```python
from lettia.protocols import AttrsBinder

payload = await AttrsBinder().bind(ctx, CreateUser)
```

## Validator protocol

```python
class Validator[T](Protocol):
    def validate(self, obj: T) -> None: ...
```

`CallableValidator` adapts a function returning `True`, `False`, or `None`:

```python
CallableValidator[T](func: Callable[[T], bool | None])
```

`False` becomes HTTP 400 with `Validation failed`. Exceptions from the user
function become HTTP 400 validation errors, while an existing
`HTTPException` is preserved.

## Renderer protocol

```python
class Renderer(Protocol):
    def render(
        self,
        template_name: str,
        context: Mapping[str, object] | None = None,
        status_code: int = 200,
    ) -> Response: ...
```

`SimpleHTMLRenderer` is a minimal in-memory renderer:

```python
renderer = SimpleHTMLRenderer({"hello": "<h1>{{name}}</h1>"})
response = renderer.render("hello", {"name": "Ada"})
```

It replaces `{{name}}` placeholders in one pass and HTML-escapes inserted
values, including quotes. Inserted placeholder-looking text is never evaluated
again; unknown placeholders remain unchanged. Templates must be trusted and
placeholders should be used as HTML text. This adapter does not provide
JavaScript, CSS, or URL-context sanitization.

**Migration:** values previously used to inject raw HTML now display as escaped
text. Use a dedicated renderer with an explicit trusted-markup policy when
intentional HTML insertion is needed.

## `StaticFiles`

```python
StaticFiles(directory: str, html: bool = False)
await static.handle(ctx: Context) -> Response
```

`StaticFiles` resolves one root directory at construction and serves only
paths that remain below that root. It supports:

- `GET` and `HEAD`;
- directory `index.html` when `html=True`;
- content SHA-256 ETags and `If-None-Match` → 304 (hashing reads the entire file);
- byte ranges → 206 and `Content-Range`;
- streaming file responses; and
- 403/404/405/416 boundary errors.

If-None-Match supports wildcard, lists, and weak comparison before range
evaluation. If-Range must match the strong ETag; other validators return a full
response. HEAD ignores Range. Unsatisfiable ranges include `bytes */size` in
Content-Range. See [static files](../static_files.md) for conditional semantics.

Mount it as a wildcard route:

```python
static = StaticFiles("public", html=True)
app.add_route("GET", "/*filepath", static.handle)
```

## Package boundaries

| Import | Role |
|---|---|
| `lettia.protocols` | Binder, validator, and renderer contracts/adapters |
| `lettia.ext` | Optional application extensions such as `StaticFiles` |
| `lettia.testing` | Test client integration built on HTTPX |

These modules can evolve independently of the ASGI dispatch core.
