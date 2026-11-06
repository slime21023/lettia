# lettia documentation

lettia is a contract-first ASGI framework for Python >= 3.12. You declare an HTTP API as `typing.Protocol` classes with typed requests, responses and guards; lettia mounts an implementation of that contract, decodes and encodes with `msgspec`, and derives an OpenAPI 3.1 document from the same types. A type checker verifies that implementations match the contract. The layer underneath (`lettia.core`) is a small ASGI toolkit usable without any contract. The only runtime dependency is `msgspec`.

## Documents

| File                                     | Contents                                                       |
| ---------------------------------------- | -------------------------------------------------------------- |
| [getting-started.md](getting-started.md) | Install, first contract app, run, test, export OpenAPI         |
| [core.md](core.md)                       | The core layer: apps, routing, requests, responses, middleware |
| [contract.md](contract.md)               | Contracts: `op`, requests, responses, mounting, startup checks |
| [guards.md](guards.md)                   | Declaring, combining and implementing guards                   |
| [openapi.md](openapi.md)                 | OpenAPI derivation, export CLI, docs UI                        |
| [websocket.md](websocket.md)             | WebSockets in core and in contracts                            |
| [testing.md](testing.md)                 | `make_context` and `TestClient`                                |
| [extensions.md](extensions.md)           | Dependency injection, background tasks, static files, docs viewers, SSE, sessions, rate limiting |

## Package map

| Module                | Layer / role                                                                                   |
| --------------------- | ---------------------------------------------------------------------------------------------- |
| `lettia.core`         | ASGI adapter, routing, request/response, context, errors, WebSocket. Knows nothing above it.   |
| `lettia.middleware`   | Built-in middleware: `access_log`, `cors`, `request_id` / `RequestId`. Depends on core only.   |
| `lettia.contract`     | Contract machinery: operations, request/response types, codec, guards, mounting, OpenAPI.      |
| `lettia`              | Public entry point; `lettia.App` is the core `App` plus `mount` and OpenAPI serving.           |
| `lettia.testing`      | `make_context`, and `TestClient` (needs `httpx`).                                              |
| `lettia.cli`          | The `lettia` command (`lettia openapi export`).                                                |
| `lettia.extensions.*` | `di`, `tasks`, `staticfiles`, `docs`, `sse`, `sessions`, `ratelimit`.                         |

### Top-level exports

`lettia.__all__` is exactly:

`ApiKeyHeader`, `App`, `Body`, `Context`, `Cookie`, `Created`, `File`, `Form`, `Group`, `GuardSpec`, `Header`, `HeaderName`, `HttpBearer`, `Key`, `NoContent`, `NotFound`, `Ok`, `Path`, `Problem`, `Query`, `Reply`, `RequestHead`, `UploadFile`, `WebSocket`, `WebSocketDisconnect`, `WebSocketError`, `op`, `public`.

They are resolved lazily on first attribute access, so `import lettia` alone loads no submodule.

### Layering rule

Lower layers do not import higher ones, and `import lettia.core` does not load the contract layer. This is enforced by `tests/architecture`:

- `test_import_independence.py` runs fresh interpreters: after `import lettia.core` or `from lettia.core import App, Router`, no `lettia.contract*` or `lettia.app` module is in `sys.modules`; the same holds for `import lettia.middleware`. `import lettia` loads only `lettia`; `from lettia import App` loads `lettia.app` and `lettia.contract` on first use.
- `test_layers.py` checks imports statically: `core` may not import `contract`, `middleware`, `testing`, `cli` or `extensions`; `middleware` may not import `contract`, `testing`, `cli` or `extensions`; `contract` may not import `middleware`, `testing`, `cli` or `extensions`; `cli` and `testing` may not import each other, `middleware` or `extensions`; `extensions` may not import `cli` or `testing`. No layer imports the top-level `lettia` package.

Practical consequence: a service that only needs routing and middleware can `from lettia.core import App` and stay free of the contract layer.

## See also

- [getting-started.md](getting-started.md)
- [contract.md](contract.md)
- [core.md](core.md)
