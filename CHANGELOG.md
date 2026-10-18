# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

### Changed
- Bumped version to `0.3.1`.
