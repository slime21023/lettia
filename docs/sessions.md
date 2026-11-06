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

## Using a session in a guard

A guard sees only the `RequestHead`, so it cannot use `Session.of(c)`. `SessionMiddleware.read(head)` reads the session from the head instead: it returns the session data as a read-only mapping, or an empty one when the request carries no valid session.

```python
from lettia import ApiKeyCookie, App, GuardSpec
from lettia.core import RequestHead, Unauthorized
from lettia.extensions.sessions import SessionMiddleware

sessions = SessionMiddleware([b"a-secret-key-of-at-least-32-bytes!"])
logged_in = GuardSpec[User]("session", scheme=ApiKeyCookie("lettia_session"))


async def verify_session(head: RequestHead) -> User:
    name = sessions.read(head).get("user")
    if not isinstance(name, str):
        raise Unauthorized("sign in")
    return User(name)


app = App(guards=[logged_in.use(verify_session)])
app.use(sessions)  # the same instance: it sets the cookie that the guard reads
```

- Use the instance you pass to `app.use`: `read` applies its keys, cookie name, lifetime and size limit, so a cookie is accepted by the guard exactly when the middleware would load it.
- The mapping is empty for a missing cookie, a forged or expired one, a cookie signed with an unknown key, and a cookie name that appears twice. A guard that does not find what it needs raises `Unauthorized`.
- `read` writes nothing: it does not refresh the cookie, drop an expired one or touch flash messages (they are not in the mapping). The middleware does that on the response.
- `ApiKeyCookie` is documentation for OpenAPI (`apiKey` in a cookie); the guard still does the reading. Give it the cookie name the middleware uses.
- The limits of the cookie still apply: a logout only deletes the browser's copy, and a stolen cookie works until it expires.

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
