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
