# Changelog

All notable changes to Lettia are documented here.

## [Unreleased]

### Changed

- Migrate core behavioral tests to Hypothesis properties with reference models
  for state, rate limits, routing, and WebSocket transitions; retain API,
  lifecycle, documentation, and focused regression tests.
- Sign session data with a version and issue time; enforce positive `max_age`
  server-side. **Old session cookies are invalidated; users must sign in again.**
- Limit attrs/dataclass binding to scalar and nullable annotations. Invalid
  input returns 400; unsupported schemas raise `TypeError`. Use PydanticBinder
  for complex models.
- Escape SimpleHTMLRenderer values in one pass. Previously injected raw HTML
  now displays as text.
- Compute strong static-file ETags from content SHA-256 on every request,
  including HEAD/304. This adds O(file size) reads with bounded memory.

### Fixed

- Recheck request body limits against cached bytes.
- Preserve outer middleware on error responses and log unexpected errors once.
- Keep upstream TimeoutError distinct from framework deadline expiry.
- Close response iterators on failures/cancellation; avoid duplicate stream
  endings and retries after send failures.
- Suppress bodies and Content-Length on 204/304 responses.
- Serve index files at empty wildcard mounts and reject final index symlinks
  escaping the static root.
- Merge CORS Vary tokens without losing existing values.

## [1.0.1] - 2026-09-08

### Changed

- Made `Router` and `Route` generic so higher-level frameworks retain their
  handler type through route matching.
- Made `App` slots-based; application objects no longer accept dynamic
  attributes.
- Removed `Any` from framework source and consolidated dynamic binding and
  HTTPX adaptation at documented typed boundaries.
- Published handler contracts and migrated tests, examples, and API reference
  signatures to the same typed ASGI boundary.
- Added typed ASGI HTTP, WebSocket, lifespan, and JSON contracts under
  `lettia.asgi`.
- Added strict Pyrefly project configuration and public API coverage policy.

### Fixed

- Terminate streamed HTTP responses when an iterator fails after headers have
  been sent.

## [1.0.0] - 2026-08-01

### Added

- ASGI application dispatch for HTTP, WebSocket, and lifespan scopes.
- Static, parameterized, wildcard, named, and method-aware routing.
- Pre-routing, global, route, and group middleware chains.
- Lazy request parsing, typed binding, response normalization, cookies, and
  streaming responses.
- Built-in recovery, CORS, request ID, timeout, body limit, rate limit, and
  signed-session middleware.
- Binder, validator, renderer, static-file, and HTTPX testing extensions.
- Behavioral tests, regression coverage, benchmarks, and component API docs.

### Quality

- 54 tests passing with an 80% coverage gate at the time of the 1.0.0 release.
- Ruff, Pyright, compileall, package builds, Twine checks, and strict docs
  builds are part of the release validation workflow.
