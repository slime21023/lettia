# Bottom-up contracts and TDD implementation

## Objective
Implement the accepted six-stage plan without changing public APIs or adding dependencies.
Preserve existing workspace changes. Separate behavior fixes from structural refactoring.

## Stages
1. Contracts and baseline — complete
2. Test classification, preserving all cases and assertions — complete
3. Pure-rule extraction and dependency checks — complete
4. Writer ownership and request-policy interfaces — complete
5. Integration audit and proven-equivalent deduplication — complete
6. Real-server smoke tests, documentation and local validation — complete; hosted CI acceptance pending

## Decisions
- Registry: TOML; explicit contract markers, layers derived from directories.
- Public write returns None; internal delivery returns completion and exposes a read-only replacement query.
- Context owns receive/cache/rejection; Writer owns send/disconnect coordination/cleanup.
- Full default pytest includes every layer; focused runs use --no-cov.
- Preserve the existing Windows/Linux and Python 3.12–3.14 CI matrix.

## Errors and resolutions
- Migration script second run encountered missing former support files; made the cleanup idempotent.
- Windows default decoding failed for Unicode test data; all migration reads now specify UTF-8.
- Merged imports produced duplicate bindings; deduplicated imports only, then checked original test ASTs.
- Extracted conditional helper initially shadowed a local variable; integration and type checks caught it, renamed the local and restored the import.
- Two coverage runs overlapped during that correction; discarded their coverage reports and reran serially (94.58%).
- Corrected a PowerShell quoting error by applying the registry edit directly.
- Final snapshot audit initially used the wrong backup root; corrected the root and verified all original functions and changed source files.
- Local WSL lacks pytest and pip; Linux validation is explicitly left to the preserved hosted CI matrix, not reported as passed.

## Delivery status
- All implementation, documentation and local quality checks completed.
- Windows Python 3.12, 3.13 and 3.14: 618 passed / one symlink privilege skip each.
- Hosted six-job Linux/Windows quality matrix remains external acceptance; no push or remote CI run was requested.

## Follow-up: residual code and simplification review
- [x] Trace private helpers, compatibility imports, test support and historical paths.
- [x] Fix Context smart binding masking Pydantic TypeError/ImportError, with failing regressions first.
- [x] Remove only redundant internal delegation/casts and stale comments; keep lifecycle safeguards and public compatibility exports.
- [x] Verify focused behavior, complete tests, typing, lint and documentation; record follow-up results separately from the prior cross-version baseline.

## Documentation update plan after implementation commit

Implementation baseline: `10d2d32` (Stabilize component contracts and reorganize
layered tests). Pushed to origin/main at the user's request; CI run 37116113235
passed all six quality jobs and package validation.
The following documentation work is planned separately from that implementation.

| Order | Scope | Changes | Acceptance |
|---|---|---|---|
| 1 | docs/architecture.md, README.md, docs/index.md | Add responsibility-layer and request-sequence diagrams; show Context receive ownership, Writer send ownership, deferred policies, error replacement cutoff and conditional background work. Keep overview pages concise and link to the detailed architecture. | Trace every flow against App/Context/Writer; separate HTTP, WebSocket and lifespan branches; render Mermaid in the documentation preview. |
| 2 | docs/api/app.md, docs/api/context.md, docs/api/response.md, docs/api/middleware.md, docs/api/extensions.md | Align owner/input/output/error/side-effect descriptions; distinguish public write() from private collaboration; explain Pydantic programming-error propagation and shared header rules. | Public signatures match code; private helpers are described as internals, not application APIs; examples use current state and binding APIs. |
| 3 | docs/context_and_binding.md, docs/middleware.md, docs/static_files.md, docs/deployment.md | Link contract details from practical examples for binding, CORS/session finalization, trusted proxy rate keys, cache/range ordering and stream cleanup. Consolidate duplicated explanations by reference. | Examples agree with regression tests; no implied durable background queue or cross-worker memory limit; preserve documented security boundaries. |
| 4 | docs/testing.md, docs/api/testing.md, README.md, docs/deployment.md, docs/testing_audit.md, CHANGELOG.md | Align verification entry points and link to one canonical command list; show the verified ty version, Pyrefly scope and test layers. Clearly label historical snapshots and current local results. | No historical counts rewritten as current results; distinguish local checks, optional ty checks and actual CI gates; documentation policy tests and strict build pass. |

- [x] Inspect current documentation and establish the update order.
- [x] Update and visually verify the architecture diagrams.
- [x] Reconcile component API contracts and cross-links.
- [x] Align guides, examples and verification instructions.
- [x] Run documentation-policy tests, strict build and link/diagram review.
- [x] Package the verified documentation as a separate commit from the implementation.

Keep the existing documentation language and navigation. Update zensical.toml
only if a new page is actually needed. Do not change source behavior or claim
hosted CI success as part of this documentation task. Preserve the recorded
intermittent Hypothesis failure as an open verification observation.
