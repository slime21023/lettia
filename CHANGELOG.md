# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
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
