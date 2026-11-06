# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.3] - 2026-11-06

### Added
- `SessionMiddleware.read(head)`: the session data a request carries, as a read-only mapping, for use in a contract guard (a guard sees only the `RequestHead`, so it cannot use `Session.of(c)`). It reads the cookie exactly as the middleware does, with the same keys, name, lifetime and size limit, and writes nothing. The mapping is empty for a missing, forged, expired or repeated cookie; flash messages are not part of it.
- `ApiKeyCookie(name)`, a security scheme for a guard that reads a cookie; OpenAPI describes it as `{"type": "apiKey", "in": "cookie", "name": name}`.

### Changed
- Bumped version to `0.5.3`.

## [0.5.2] - 2026-11-06

### Added
- Contract response classes `Accepted[T]` (202), `BadRequest[T]` (400), `Conflict[T]` (409) and the redirects `MovedPermanently` (301), `Found` (302), `SeeOther` (303), `TemporaryRedirect` (307) and `PermanentRedirect` (308). A redirect takes its `location` as the required `Location` header and has no body; OpenAPI lists the status, its reason and the header.
- `Context.redirect(location, status=302)` for Core handlers; a status other than 301, 302, 303, 307 or 308, an empty location or one with a line break is a `ValueError`.
- `Response.set_cookie(...)` and `Response.delete_cookie(...)`: `HttpOnly` and `SameSite=Lax` by default, several cookies side by side, validation of name, value, path and domain against header injection, the `__Secure-` and `__Host-` rules, and a refusal of cookies browsers would drop (over 4096 bytes).
- `Container.wire(app, services, *, middleware=None)` forwards the `mount(middleware=...)` mapping to the service that implements each contract method; an entry no wired service implements is a startup problem.
- Documentation for putting compression, host checks and HTTPS redirects in front of the app as ASGI middleware, including keeping server-sent events out of compression.

### Changed
- `lettia.extensions.sessions` writes its cookie with the new Core serializer instead of `http.cookies`; the cookie has the same meaning, with the attributes in a fixed order, and a cookie over 4096 bytes is refused with a `ValueError` from the serializer.
- Bumped version to `0.5.2`.

## [0.5.1] - 2026-11-06

### Added
- `middleware=` on `route`, `get`, `post`, `put`, `patch`, `delete` and `options` (on `App` and `Group`): a sequence of middleware that wraps one route, inside the group middleware, with error conversion at every boundary like any other middleware. A single callable instead of a sequence raises `TypeError`.
- `mount(implementation, *, codec=None, middleware=None)`: `middleware` maps contract methods (for example `Api.login`) to the middleware that wraps only that operation, outside its guard. A key that is not an operation of a mounted contract, or one for a WebSocket operation, is a startup problem. The contract itself is unchanged.

### Changed
- Bumped version to `0.5.1`.

## [0.5.0] - 2026-11-06

The planned 0.4.1, 0.4.2 and 0.4.3 milestones were built and verified together and ship as this release; the extensions below are no longer marked experimental.

### Added
- `lettia.extensions.docs`: `mount_docs(app, prefix="/docs", provider="scalar", *, title=None, favicon=None, theme="auto", assets=None)` for Scalar, Swagger UI and the new Redoc, with light/dark/auto themes, a favicon, and an offline mode that serves the pinned bundles from a directory (hash-checked at startup, strict CSP on the page). `App(docs_url=..., docs_ui=...)` now delegates to it and accepts `docs_ui="redoc"`; `App.title` and `App.openapi_url` are public read-only properties.
- `lettia.extensions.sse`: `ServerSentEvent`, `EventStream[T]` (a contract response class) and `event_stream(...)` for core routes. JSON `data`, validated `event`/`id`/`retry`, `: connected` first comment, `: keep-alive` heartbeats that never cancel the producer, `Cache-Control: no-cache` and `X-Accel-Buffering: no`.
- `lettia.contract.StreamReply[T]`: the generic hook for a response whose body is an `AsyncIterator[T]` (compiled at mount time, reflected in OpenAPI as a `text/event-stream` string schema plus `x-lettia-item-schema`).
- `Response(watch_disconnect=True)`: the ASGI adapter watches `receive` while a body streams, cancels the stream and closes the source when the client disconnects, and then skips `after_send`. Off by default; other responses take the old path.
- `lettia.extensions.sessions`: `SessionMiddleware` and `Session.of(c)`, a cookie signed with HMAC-SHA256 (readable, not encrypted) with flash messages, key rotation, server-checked expiry and strict parsing. No server-side storage, so no revocation.
- `lettia.extensions.ratelimit`: `rate_limit("5/minute")` middleware with an in-memory sliding window per client IP (or a custom `key=`), `429` problem responses with `Retry-After`, `X-RateLimit-*` headers and a bounded number of identities (`503` rather than eviction when full). One process only.
- Guides: `docs/sessions.md`, `docs/ratelimit.md`; sections for the new extensions in `docs/extensions.md`, `docs/contract.md`, `docs/core.md` and `docs/openapi.md`.

### Changed
- Swagger UI is pinned to 5.34.1 (native dark mode); Scalar stays at 1.60.0; Redoc is pinned to 2.5.4. All CDN files carry SRI hashes.
- `lettia.docs` moved to `lettia.extensions.docs.pages`; the old module is gone.
- Bumped version to `0.5.0`.

## [0.4.0] - 2026-11-05

### Added
- `Response(after_send=...)`: a synchronous callback the ASGI adapter calls once, after the whole response was sent and a streamed body was closed. It is not called when the handler, the send or the stream fails or the request is cancelled.
- Documentation: WebSocket `Origin` checking (`docs/websocket.md#security`), sending a declared `Problem` reply as `application/problem+json`, the limits of group middleware (404/405 and WebSocket routes are not covered), and the lifetime of request-scoped resources in background tasks.

### Changed
- Background tasks now start after the response was sent successfully instead of as soon as the handler returns. They no longer start when the send fails, a streamed body fails or the request is cancelled, nor when an outer middleware replaces the response. Tasks queued before a handler error still run once the resulting 500 was sent.
- A background task can no longer resolve `Injected` attributes even when `DIMiddleware` is the outer middleware; it raises `RuntimeError` like with the other order. The request scope is closed before any task runs, so tasks must open the resources they need.
- Bumped version to `0.4.0`.

### Fixed
- The startup problem for a wrong WebSocket operation signature now says `(self, c, ws)` instead of `(self, c, req)`.
- `StaticFiles.mount(app, "/", directory)` no longer raises `ValueError`; it registers `GET /*` and `GET /`.
- `StaticFiles` handles `If-None-Match` lists, weak tags and `*`, and ignores `If-Modified-Since` when `If-None-Match` is present (RFC 9110 section 13.1.3).
- Background tasks given a sync callable that returns an awaitable (lambda, `functools.partial`, object with `async def __call__`) now await it instead of dropping the coroutine.
- An invalid response header name (`HeaderName("Bad Name")`) is reported as a startup problem instead of raising `ValueError` from `mount()`.
- A response or request body type with no JSON Schema (for example `dict[str, object]`) is reported as a startup problem (`OpenAPI schemas: cannot generate a JSON schema`) instead of an unhandled `TypeError`.
- `Container.wire()` now binds `Injected` declarations inherited from base classes, written as strings (`from __future__ import annotations`), or declared as `ClassVar[Injected[T]]`, which is the form to use on `@dataclass` services.
- `Container.wire()` reports a scoped constructor dependency (directly or through a transient) as a startup problem and does not mount the service, instead of silently sharing one scoped instance, never torn down, across all requests. The check stops before the scoped factory runs. Declare such dependencies as `Injected[T]`.
- `Container.wire()` raises a clear `TypeError` for a bare `Injected` / `ClassVar[Injected]` annotation and for an unresolvable `Injected[...]` string annotation (including through an import alias); a bare `Injected` annotation no longer makes the descriptor try to resolve `Injected` itself.

## [0.3.1] - 2026-10-18

### Added
- **Core WebSocket Support**:
  - Full ASGI 3.0 bidirectional WebSocket implementation (`WebSocket`, `WebSocketDisconnect`, `WebSocketError`).
  - Pre-handshake authentication guards on WebSocket routes (`@op.websocket("/ws", guard=...)`).
  - Native Radix router integration supporting full static, parameter, and wildcard routes for WebSockets.
- **Dependency Injection Extension (`lettia.extensions.di`)**:
  - Declarative dependency injection via `Injected[T]` descriptor backed by `rodi`.
  - Zero-overhead handler signatures: preserves explicit `(self, c, req)` signatures without per-request reflection.
  - Automated request-scoped async teardown (`aclose` / `close`).
  - Decoupled `__injected__` protocol hook for third-party extensions.
- **Background Tasks Extension (`lettia.extensions.tasks`)**:
  - Lightweight in-process background task queue (`BackgroundTasks`, `BackgroundTask`, `BackgroundTasksMiddleware`).
  - Non-blocking sequential FIFO execution dispatched after HTTP response delivery.
  - Integration with ASGI Lifespan shutdown draining (`middleware.drain(timeout_sec=...)`).
  - Dual ergonomics via `BackgroundTasks.of(c)` and declarative `Injected[BackgroundTasks]`.
- **Static Files Serving Extension (`lettia.extensions.staticfiles`)**:
  - High-security static file server (`StaticFiles`) with CWE-22 Path Traversal and dotfile defenses.
  - RFC 7232 conditional caching (`ETag`, `If-None-Match`, `Last-Modified`, `If-Modified-Since`, `304 Not Modified`).
  - RFC 7233 single-part byte-range parsing and partial content streaming (`HTTP 206 Partial Content`, `416 Range Not Satisfiable`).
  - Non-blocking 64KB async chunk streaming via `asyncio.to_thread`.
  - SPA (Single Page Application) HTML5 fallback support (`html=True`).
  - One-liner mount helper: `StaticFiles.mount(app, prefix, directory)`.
- **API Docs UI**:
  - `App(docs_url="/docs", docs_ui="scalar" | "swagger")` serves an interactive OpenAPI viewer loaded from a version-pinned CDN file with a Subresource Integrity hash.

### Changed
- Bumped version to `0.3.1`.
