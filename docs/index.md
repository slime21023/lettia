---
title: Lettia Documentation
---

# Build applications with Lettia

Lettia is a Python 3.12+ framework for HTTP APIs and WebSocket endpoints. These
guides help application developers install the framework, write routes,
validate input, test behavior and deploy a service.

## Start with a working application

Follow [Build your first app](getting_started.md) for the files to create,
commands to run and expected responses. You do not need to study framework
internals before using Lettia.

```python
from lettia import App, Context

app = App()


@app.get("/hello/:name")
def hello(ctx: Context) -> dict[str, str]:
    return {"message": f"Hello, {ctx.path_params['name']}"}
```

Save this as `app.py` after installing Lettia and Uvicorn, then run
`uv run uvicorn app:app --reload`. GET `/hello/Ada` returns HTTP 200 with
`{"message": "Hello, Ada"}`.

## Choose your path

| If you are… | Start with |
|---|---|
| New to Lettia | [Getting started](getting_started.md) |
| Returning status codes, headers, cookies or streams | [Responses and errors](responses.md) |
| Building an HTTP API | [Routing](routing.md), then [Context and binding](context_and_binding.md) |
| Adding authentication, CORS, or limits | [Middleware](middleware.md) |
| Deploying an application | [Deployment](deployment.md) |
| Building a real-time endpoint | [WebSockets](websocket.md) |
| Serving local assets | [Static files](static_files.md) |
| Maintaining a test suite | [Testing](testing.md) |
| Looking for a signature | [API reference](api_reference.md) |
| Upgrading an existing application | [Release notes](migrations/v1-rc2.md) |

## The application model

Register a handler on `App`. Lettia passes it a `Context` containing request
data and converts the handler's return value into an HTTP response.

Return a dictionary for JSON, a string for text, or an explicit `Response` when
you need status, headers or cookies. Use `ctx.bind()` for typed input and
`ctx.abort()` for expected HTTP errors. Middleware adds behavior shared by
multiple routes.

Keep request-specific values in `ctx.state`; pass long-lived services into route
setup through an application factory. Your application chooses its database,
configuration and durable job queue. The ASGI server and hosting platform
provide TLS, proxy trust and worker management.

## Advanced reading and framework contributions

The [architecture diagrams](architecture.md) explain internal responsibility
and lifecycle boundaries for deeper debugging and framework contributions.
The [repository quality gates](contributing_testing.md#coverage-and-quality-gates) and
[migration audit](testing_audit.md) describe Lettia maintenance; they are not
setup requirements for an application using Lettia.
