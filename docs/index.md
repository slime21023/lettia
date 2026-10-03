---
title: Lettia Documentation
---

# Lettia documentation

Lettia is an explicit, type-first ASGI core for Python 3.12+. This documentation is
organized around the way an application is built: start with a working app,
learn the request model, then add routing, middleware, integrations, and
tests.

## Start with the system model

Before reading individual APIs, choose the area relevant to your application:

| Area | Components | Question it answers |
|---|---|---|
| ASGI runtime | `App`, `Context`, `Router`, `ResponseWriter` | How does a scope become a response? |
| Composition | `Route`, `Group`, middleware | How are application behaviors assembled? |
| Extensions | binders, validators, renderers, `StaticFiles` | How are integrations added without changing the core? |
| Verification | `TestClient`, pytest, benchmarks | How are contracts and performance checked? |

The [Architecture](architecture.md) page explains the boundaries and request
lifecycle. The [API reference overview](api_reference.md) then routes advanced
readers to a component-specific reference page.

## Choose your path

| If you are… | Start with |
|---|---|
| New to Lettia | [Getting started](getting_started.md) |
| Familiar with ASGI | [Architecture](architecture.md) |
| Building an HTTP API | [Routing](routing.md), then [Context and binding](context_and_binding.md) |
| Adding authentication, CORS, or limits | [Middleware](middleware.md) |
| Deploying an application | [Deployment](deployment.md) |
| Building a real-time endpoint | [WebSockets](websocket.md) |
| Serving local assets | [Static files](static_files.md) |
| Maintaining a test suite | [Testing](testing.md) |
| Looking for a signature | [API reference](api_reference.md) |

## The core mental model

An incoming HTTP request moves through five stages:

1. `App` receives an ASGI scope, `receive`, and `send` callable.

2. App creates `Context`; pre-routing and then global middleware run before
   route matching. They can return early, including for CORS preflight.

3. The router populates `ctx.path_params` and enters route/group middleware
   around the selected handler, or produces a 404/405 error.

4. The handler result becomes a `Response`, which returns through the
   middleware chain. Writer applies deferred policies and validates it.

5. `ResponseWriter` sends the response and cleans up its resources. App runs
   queued background tasks only when Writer reports completion eligibility.

See [Architecture](architecture.md) for the complete HTTP, WebSocket, and
lifespan flow.

## What belongs in the core

- `App`: ASGI entrypoint, routes, middleware, lifespan hooks, and errors.
- `Router`: static, parameterized, wildcard, named, and method-aware routes.
- `Context`: lazy request data, state, binding, aborts, and post-response tasks.
- `Response`: text, JSON, bytes, streams, headers, and cookies.
- `WebSocketContext`: explicit accept, receive, send, and close transitions.

Extensions such as binders, validators, renderers, static files, and
`TestClient` remain small and composable. Start with
[Getting started](getting_started.md), then use the topic guides as your
application grows.

## Operating model

Lettia owns typed ASGI dispatch, HTTP request state, response emission, and
WebSocket state transitions. Your composition root owns application services;
your ASGI server and platform own TLS, proxy trust, worker lifecycle, and
distributed infrastructure. This separation is intentional: `App` has no
global mutable state container, and `Context.state` / `WebSocketContext.state`
are scoped to one request or one connection respectively.
