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
If a directory with an index is requested without its trailing slash, Lettia
first returns a 307 redirect to the slash-terminated path, preserving the query
string. This keeps relative links and assets inside the directory. Path safety
and index existence are checked before redirecting; conditional headers and
ranges are evaluated on the canonical URL.
ASGI `root_path` is excluded from filesystem lookup and retained in redirect
locations. This also applies when `StaticFiles.handle` is registered directly
without a `filepath` wildcard parameter.

## Response behavior

| Request | Result |
|---|---|
| Existing file | 200 with ETag and content length |
| HTML directory with an index, missing trailing slash | 307 to the directory URL with `/` |
| Matching `If-None-Match` (GET or HEAD) | 304 |
| Valid GET `Range`, with absent or matching strong `If-Range` | 206 with `Content-Range` |
| Non-matching, weak, invalid, or date-based `If-Range` | Full 200 response |
| HEAD with Range | Normal HEAD representation headers; no partial response |
| Unsatisfiable range | 416 with `Content-Range: bytes */size` |
| Unknown file | 404 |
| Invalid or escaping path | 403 |
| Unsupported method | 405 |

Examples of supported ranges include `bytes=0-99`, `bytes=100-`, and
`bytes=-100`. Multiple ranges are rejected rather than silently served as a
full response.

If-None-Match is evaluated before ranges. It accepts `*`, a list of quoted
ETags, and weak tags (`W/"..."`); commas inside quoted tags remain part of the
tag. Malformed conditions are ignored. If-Range requires an exact strong ETag
match; date conditions fall back to a full response because this extension
does not publish a Last-Modified validator.

The decision order for a canonical file URL is:

1. Resolve the path and enforce containment, including the selected index file.
2. Read file metadata and compute the content ETag.
3. Evaluate If-None-Match; a match returns 304 before considering Range.
4. For GET, evaluate If-Range and then a single Range. HEAD skips this step.
5. Return the full or partial representation, or 416 with the file size.

For example, a matching If-None-Match plus an unsatisfiable Range returns 304;
a mismatching If-Range plus that Range returns a full 200. The pure conditional
rules decide these outcomes; StaticFiles owns filesystem safety and creates
the response. Writer consumes and closes its file stream, including on
cancellation. HEAD and 304 suppress body transmission, but still incur the ETag
hash read. See the [StaticFiles API](api/extensions.md#staticfiles) and
[Writer lifecycle](architecture.md#resource-and-state-ownership).

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
