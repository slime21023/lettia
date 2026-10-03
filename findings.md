# Implementation findings

- Starting suite: 19 files, 191 test functions, 544 collected cases.
- Last full run: 543 passed, one Windows symlink privilege skip; 94.45% coverage.
- App directly controls Writer flags and Context finalizer storage.
- Response/WebSocket import JSON validation from Context.
- Historical regression files mix responsibilities; test support imports rely on tests path injection.
- Existing dependencies include Uvicorn and HTTPX; no new package is needed for socket smoke tests.

## Implementation outcome
- Pure rules are independent modules; Context retains the JSON compatibility import.
- Writer owns delivery/disconnect/cleanup state; App uses only collaboration methods and completion results.
- Context activates its own policy registry and continues to own receive/cache/limits.
- Test selection derives layers from directories and checks the single TOML registry before filtering.
- All original functions retained; one proven-equivalent matrix reduction is recorded in migration.json and testing_audit.md.
- Local Windows 3.12 validation passes 618 cases, skips one symlink privilege case, and measures 95.13% coverage.

## Residual code review outcome
- No active parallel legacy implementation found in the reviewed helper call sites and dependency paths; compatibility re-exports and migration history remain intentional.
- Context smart binding swallowed Pydantic validator TypeError/ImportError and replaced them with an attrs fallback error. A four-case direct/smart regression matrix now preserves the original exception identity.
- Removed redundant Writer delegation and dataclass cast; retained the attrs cast because Pyrefly requires it. Pure JSON import in errors can safely live at module scope.
- Current Windows 3.12 result: 622 passed, one symlink privilege skip, 95.09% coverage; 220 test functions / 623 cases / 40 contracts.

## Documentation planning after commit
- Implementation baseline committed as 10d2d32; the working tree was clean immediately afterward.
- architecture.md has a layer table and a linear HTTP flow, but needs diagrams showing ownership, policy finalization and the condition for background work.
- README.md and docs/index.md repeat high-level request flow; keep them aligned with the detailed architecture via links.
- Verification commands are repeated in README, testing, deployment and API testing pages; the pinned optional ty command currently appears only in docs/testing.md.
- Old 544/619-case figures in testing_audit.md are historical evidence, not stale values to replace globally. Add clear references to the current verification section instead.

## Documentation implementation findings
- Pushed 10d2d32 to origin/main before documentation edits. Hosted run 37116113235 passed all six quality jobs plus package validation.
- Corrected overview middleware order, conditional background completion, persistent strictest body limits and ASGITransport limitations.
- Replaced the Pydantic EmailStr example (which required an undocumented additional package) with Field constraints, and changed the background example to use typed binding.
- Browser preview caught semicolon parsing in Mermaid sequence labels despite a passing strict build. Removed the separators and simplified the layer diagram to improve readability.
- Generated-page link audit checked 22 HTML pages and 1,746 local links/anchors, excluding the generator-owned 404 page's pre-existing missing skip target. Authored links passed. Updated attrs/Pydantic/background examples executed successfully.
