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
Pydantic is imported lazily; applications that use it must install the
`pydantic` extra.

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

It performs literal `{{name}}` replacement. It is a small demonstration
adapter, not a sandboxed template engine.

## `StaticFiles`

```python
StaticFiles(directory: str, html: bool = False)
await static.handle(ctx: Context) -> Response
```

`StaticFiles` resolves one root directory at construction and serves only
paths that remain below that root. It supports:

- `GET` and `HEAD`;
- directory `index.html` when `html=True`;
- ETags and `If-None-Match` → 304;
- byte ranges → 206 and `Content-Range`;
- streaming file responses; and
- 403/404/405/416 boundary errors.

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
