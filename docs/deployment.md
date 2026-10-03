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

For a proxy that removes `/api` before forwarding requests, configure the ASGI
server's mount prefix, for example `uvicorn app:app --root-path /api`. Declare
routes relative to the application, such as `/health`. Lettia resolves the mount
for both HTTP and WebSocket routing, and static-directory redirects preserve
the external prefix.

## Application factory and lifecycle

Build long-lived dependencies in an explicit composition root. Start and close
them through lifespan hooks, then pass the dependencies into route
registration. Do not attach them to `App`.

This sketch assumes application-defined `Services` and `register_routes`.
It illustrates ownership, rather than a standalone runnable application.

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
the same application process, including successfully delivered error and fallback
responses. Tasks run once, after iterator cleanup. Disconnects, cancellation,
transport errors, source errors, and cleanup failures suppress them; a rejected
response replaced successfully before transmission does not. It is appropriate
for short, best-effort work
such as an audit notification. It is not durable: a process stop or failure
can prevent completion, and Lettia does not retry it. Use an external queue and
worker for delivery that must survive failures.

## WebSocket checklist

WebSocket scopes bypass the HTTP middleware chain. Before calling `accept()`,
validate credentials and origin policy in the handler. Set connection limits,
idle policies, message-size limits, and observability at the handler or ASGI
server layer according to the workload. Keep each connection's mutable values
in `ws.state`; do not use application-global mutable state.

## Verification before deployment

Run your application's tests, then start the actual application with its ASGI
server configuration. Verify a health endpoint, startup/shutdown hooks, proxy
settings, and any streaming or WebSocket behavior the application uses.

For the `app.py` created in [Getting started](getting_started.md), run:

```bash
uv run uvicorn app:app --host 127.0.0.1 --port 8765
```

In a second terminal, request `http://127.0.0.1:8765/health`; expect HTTP 200
with `{"status":"ok"}`. Stop the server after the check. In-process HTTP tests
do not run lifespan or reproduce socket disconnects automatically.

Framework contributors can use the separate
[verification commands](contributing_testing.md#verification-commands) and
[smoke tests](contributing_testing.md#real-server-smoke-tests). Those repository
checks are not prerequisites for deploying an application that uses Lettia.

<span id="verification-before-release"></span>
