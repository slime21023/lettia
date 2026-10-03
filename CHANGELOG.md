# Changelog

All notable changes to Lettia are documented here.

## [Unreleased]

### Changed

- Document component ownership, response replacement and completion boundaries
  with architecture diagrams. Consolidate verification commands and distinguish
  in-process HTTP tests from real-server disconnect and lifespan coverage.
- Preserve generic Binder result types and binary file-open overloads across
  Pyrefly and ty; document a reproducible ty check without adding dependencies.
- Separate JSON validation, header/cookie encoding, conditional-request rules,
  and constructor binding into private pure modules. Preserve public imports,
  response dictionaries, Binder interfaces and `ResponseWriter.write()`.
- Make Writer the sole owner of send/disconnect coordination and cleanup.
  App uses delivery results and a replacement query; Context owns policy
  registration and the request receive channel.
- Classify tests by unit, integration, smoke and architecture responsibilities.
  Enforce a TOML contract registry at collection, record lossless migration and
  audited matrix reductions, and include real Uvicorn/HTTPX tests by default.

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

- Preserve Pydantic validator programming errors through `Context.bind()`.
  Only absence of the optional package triggers binder fallback; validator
  TypeError/ImportError are no longer replaced by an unrelated attrs error.

- Validate response Content-Length by numeric value, preserving leading zeros,
  and allow explicit HEAD routes to describe a GET representation without
  constructing its body. Keep malformed/duplicate length rejection and 204/304
  body suppression at the shared writer boundary.
- Bind dataclass `InitVar` constructor parameters, including inherited and
  keyword-only parameters, through the existing scalar conversion rules;
  preserve defaults and reject unsupported annotations as configuration errors.
- Return HTTP 400 when request JSON exceeds decoder or validation recursion
  limits, consistently for direct JSON reads and all built-in binders.
- Acquire, iterate, and close response streams in the same task and Context so
  context-variable cleanup succeeds after send failures or cancellation. Keep
  repeated cancellation from interrupting cleanup and serialize writer calls.
- Skip error-response factories after response start has been attempted; log
  the failure without creating unused streams or other response resources.
- Resolve ASGI `root_path` consistently for HTTP, HEAD/405 detection, WebSocket,
  and static-file lookup. Preserve mount prefixes in static-directory redirects
  and retain the original scope path for handlers and middleware.
- Prevent disconnects or repeated external cancellation from cancelling stream
  cleanup again after a response deadline; await cleanup before propagating
  cancellation.
- Run background tasks once after successfully delivered error/fallback
  responses, while still suppressing them after transport or cleanup failures.
- Finalize built-in response policies at the writer boundary for normal,
  middleware-error, and fallback responses. Session signing uses the latest
  state after error handling; reused responses do not accumulate policy cookies.
  Within App, policy headers are now added after outer middleware returns;
  standalone middleware calls retain immediate response decoration.
- Reject invalid Content-Length syntax on streams before response start,
  allowing a valid error response while still closing the original iterator.
- Separate response transmission from iterator cleanup. Normal post-response
  disconnects and expired send deadlines no longer interrupt cleanup or suppress
  successful completion tasks; external cancellation still propagates.
- Persist body limits and rejection state in Context. Stream disconnect
  monitoring discards rejected input without caching it, and cancellation no
  longer loses partially read request chunks.
- Replay applied built-in CORS, Request ID, and Session response policies on
  writer error replacements without rerunning routes or request-side middleware.
- Extend `delete_cookie()` with compatible keyword-only security attributes;
  enable Secure for prefixed deletions and validate prefix scope for both cookie
  creation and deletion.

- Send handler-timeout responses without reusing an expired deadline; retain
  the no-retry rule for transmission failures.
- Monitor idle response streams for disconnects, await task/iterator cleanup,
  preserve lazy request-body reads, and skip completion work on disconnect.
  Reclaim static-file handles even when cancellation occurs during threaded open.
- Validate HTTP token names and header control characters before transmission;
  reject cookie delimiters and illegal octets atomically instead of allowing
  values or attributes to alter the cookie structure.
- Preserve Secure and SameSite when clearing signed sessions, including cookies
  with security prefixes.
- Redirect HTML directory requests to trailing-slash URLs before evaluating
  cache/range conditions, preserving queries and relative asset resolution.

- Unify response-header overrides across case variants and preserve existing
  Set-Cookie order; validate every cookie attribute, including `expires`, before
  mutation to prevent additional-cookie injection.
- Track response-start attempts independently of commitment; never retry after
  a transport error, cancellation, or deadline during send. Error fallbacks use
  the same writer, including HEAD body suppression.
- Reflect wildcard CORS preflight permissions explicitly for credentialed
  requests; vary all explicit-origin responses by Origin and reflected fields.
  OPTIONS without Access-Control-Request-Method now reaches routing.
- Remove implicit trust in X-Forwarded-For when the ASGI client is unknown;
  unknown clients now share a separate rate-limit bucket. Use verified server
  identity or an explicit `key_func` for trusted-proxy deployments.
- Evaluate static If-None-Match wildcard/list/weak conditions before ranges,
  require a matching strong If-Range for partial responses, ignore Range on
  HEAD, and include the representation size in 416 responses.
- Bind attrs constructor aliases and exclude non-init fields from both basic
  binders; convert float overflow and JSON integer-decoding limit failures to
  HTTP 400 while preserving unsupported-schema configuration errors.
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

- Separate JSON validation, header/cookie encoding, conditional-request rules,
  and constructor binding into private pure modules. Preserve public imports,
  response dictionaries, Binder interfaces and `ResponseWriter.write()`.
- Make Writer the sole owner of send/disconnect coordination and cleanup.
  App uses delivery results and a replacement query; Context owns policy
  registration and the request receive channel.
- Classify tests by unit, integration, smoke and architecture responsibilities.
  Enforce a TOML contract registry at collection, record lossless migration and
  audited matrix reductions, and include real Uvicorn/HTTPX tests by default.

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
