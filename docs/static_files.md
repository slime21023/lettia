---
title: Static Files
---

# Static files

`StaticFiles` serves files from one resolved directory. It supports ETags,
byte ranges, `GET`, and `HEAD`, and rejects paths that resolve outside the
configured directory.

File metadata and content reads are offloaded from the event loop, so serving a
local file does not block unrelated async requests. ETags are strong SHA-256
hashes of the file contents, recalculated on every request, including HEAD and
conditional requests. This requires O(file size) reading with bounded memory;
metadata alone is never used to reuse an ETag.

## Mount a directory

```python
from lettia import App
from lettia.ext import StaticFiles

app = App()
static = StaticFiles("public", html=True)

app.add_route("GET", "/*filepath", static.handle)
```

With `html=True`, a directory request looks for `index.html`. The router's
automatic HEAD fallback means the same GET route also serves HEAD requests
without sending a response body. A prefixed mount such as `/assets/*filepath`
also serves its index at `/assets/` (the wildcard is empty).

## Response behavior

| Request | Result |
|---|---|
| Existing file | 200 with ETag and content length |
| Matching `If-None-Match` | 304 |
| Valid `Range` header | 206 with `Content-Range` |
| Unknown file | 404 |
| Invalid or escaping path | 403 |
| Unsupported method | 405 |

Examples of supported ranges include `bytes=0-99`, `bytes=100-`, and
`bytes=-100`. Multiple ranges are rejected rather than silently served as a
full response.

## Security notes

- Resolve the directory once at startup and keep it separate from uploaded
  files unless that is intentional.

- Do not construct `filepath` from an untrusted filesystem path outside the
  route parameter; `StaticFiles` validates the resolved path, but application
  code should still avoid unnecessary path transformations.

- Final paths are checked again after selecting `index.html`, including symlink
  targets. Keep the served tree stable during requests; this is path containment,
  not protection against concurrent filesystem replacement.

- ETags provide cache validation, not access control. A 304 response has no body
  or Content-Length header.
