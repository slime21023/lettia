---
title: Deployment
---

# Deployment

Lettia is an ASGI application core. Deploy it with an ASGI server and make the
hosting platform responsible for transport security, process lifecycle, and
shared infrastructure.

## Run an application

Expose an `App` instance from an importable module and run it with an ASGI
server. For local development:

```bash
uv run uvicorn app:app --reload
```

For a production process, omit reload and configure the server, worker count,
timeouts, logging, and graceful shutdown according to the server's own
documentation:

```bash
uv run uvicorn app:app --host 0.0.0.0 --port 8000
```

Lettia does not terminate TLS or manage workers. Put a TLS-capable reverse
proxy or load balancer in front of public deployments.

## Application factory and lifecycle

Build long-lived dependencies in an explicit composition root. Start and close
them through lifespan hooks, then pass the dependencies into route
registration. Do not attach them to `App`.

```python
from lettia import App


def create_app(services: Services) -> App:
    app = App()

    @app.on_event("startup")
    async def startup() -> None:
        await services.start()

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await services.close()

    register_routes(app, services)
    return app
```

This keeps import direction explicit and makes application construction easy to
test. Request-local data belongs in `Context.state`; WebSocket connection data
belongs in `WebSocketContext.state`.

## Public HTTP checklist

- Terminate TLS before traffic reaches the application.
- Configure which proxies are trusted before interpreting forwarded client
  headers. Do not treat arbitrary `X-Forwarded-For` values as identity.
- Use an explicit CORS origin list for browser-facing APIs.
- Install `body_limit()` with an endpoint-appropriate maximum.
- Use `session(..., https_only=True)` for cookies delivered over HTTPS, and do
  not place confidential data in signed session payloads.
- Put multi-worker or multi-instance rate limiting in an API gateway or shared
  service. `MemoryRateLimiter` is intentionally process-local.
- Send structured logs, metrics, and traces to the observability system used by
  the host platform.

## Background tasks and reliability

`ctx.add_background_task()` runs after the HTTP response has been written in
the same application process. It is appropriate for short, best-effort work
such as an audit notification. It is not durable: a process stop or failure
can prevent completion, and Lettia does not retry it. Use an external queue and
worker for delivery that must survive failures.

## WebSocket checklist

WebSocket scopes bypass the HTTP middleware chain. Before calling `accept()`,
validate credentials and origin policy in the handler. Set connection limits,
idle policies, message-size limits, and observability at the handler or ASGI
server layer according to the workload. Keep each connection's mutable values
in `ws.state`; do not use application-global mutable state.

## Verification before release

Run the project quality gates before releasing a framework or deploying a
service change:

```bash
uv run pytest
uv run ruff check .
uv run pyright
uv run pyrefly check
uv run zensical build --strict
```

For a real local server smoke check, run the example and query it over a
loopback socket:

```bash
uv run uvicorn examples.rest_api:app --host 127.0.0.1 --port 8765
curl --fail http://127.0.0.1:8765/api/v1/users/
```

Run load, failure, and server-integration tests in the target environment as
well. Unit coverage validates Lettia's contracts; it does not replace capacity
or infrastructure validation.
