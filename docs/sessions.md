# Sessions

`lettia.extensions.sessions` keeps per-browser state in a **signed** cookie.
No server-side storage, database or Redis is involved.

```python
from lettia.core import App, Context
from lettia.extensions.sessions import Session, SessionMiddleware

app = App()
app.use(SessionMiddleware([b"a-secret-key-of-at-least-32-bytes!"]))


async def login(c: Context):
    session = Session.of(c)
    session["user"] = "ann"
    session.flash("Welcome back", "info")
    return c.text(200, "ok")


async def home(c: Context):
    session = Session.of(c)
    return c.json(200, {"user": session.get("user"), "flashes": session.pop_flashes()})


app.post("/login", login)
app.get("/", home)
```

`Session.of(c)` raises `RuntimeError` when the middleware is not installed.
`Session` is a `MutableMapping[str, SessionValue]`; values must be JSON-like
(`str`, `int`, `float`, `bool`, `None`, lists and string-keyed dicts nested at
most 32 levels). Anything else makes the response fail with `ValueError`; it is
never silently truncated.

## Configuration

`SessionMiddleware(secret_keys, *, cookie_name="lettia_session",
max_age=1_209_600, same_site="lax", secure=True, path="/",
max_cookie_bytes=4096)`

- `secret_keys`: non-empty sequence of `bytes`, each at least 32 bytes. The
  first key signs, all keys verify, so you can rotate by putting the new key
  first. A token signed by an older key is re-signed without extending its
  expiry.
- The cookie is always `HttpOnly`, never has a `Domain`, and is `Secure` by
  default. `same_site="none"` requires `secure=True`; `__Secure-`/`__Host-`
  names are checked. Invalid configuration, including a limit too small for an
  empty signed cookie, fails in the constructor.
- A cookie that would exceed `max_cookie_bytes` fails the request with
  `ValueError`.

## Behaviour

- Changes are detected by comparing canonical JSON before and after the
  handler, so nested mutations are persisted. Unchanged valid sessions are not
  re-sent; clearing an existing session deletes the cookie.
- Responses with status 500 or above never commit the session. Changes made
  after the response was returned, or after streaming started, are not
  persisted. Headers are fixed before sending; no `after_send` is used.
- Tokens have a fixed lifetime (`iat`/`exp`); there is no rolling expiry.
  A token whose expiry passes while the handler runs is deleted, not renewed.
  `iat` may be up to 60 seconds in the future to tolerate clock skew.
- Duplicate cookies of the same name, quoted values, malformed or oversized
  tokens, unknown versions and wrong keys all yield an empty session.
- If committing fails, the original response body (for example a stream) is
  closed before the error propagates and its `after_send` is dropped.
- Flash messages are separate from the data. Unread ones persist until read or
  expiry; flashes added during a request show up on a later one. Exactly-once
  delivery is not guaranteed for parallel requests.
- WebSocket routes do not get the middleware automatically.

## Security boundaries

- **Signed, not encrypted.** The payload is only base64url-encoded JSON:
  anyone holding the cookie can read it. Never store secrets in the session.
- **No server-side revoke and no replay protection.** Logging out only deletes
  the cookie in the browser; a copied token stays valid until it expires.
- **No merging of parallel requests**: the last committed response wins.
- `SameSite` is not a complete CSRF defence.
- There is no session store, background cleanup or lifespan hook.
- An outer middleware that replaces the response must keep its `Set-Cookie`
  headers itself.
