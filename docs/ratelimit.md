# Rate limiting

`lettia.extensions.ratelimit.rate_limit` is a middleware factory that limits
requests per identity inside **one process**, with an in-memory sliding window.
It needs no external backend.

```python
from lettia.core import App
from lettia.extensions.ratelimit import rate_limit

app = App()
app.use(rate_limit("100/minute"))  # whole app, including 404/405

api = app.group("/api", rate_limit("5/minute"))  # only routes of this group
```

`rate_limit(rule, *, key=client_ip, max_keys=10_000, clock=time.monotonic)`

- `rule` is `<n>/<second|minute|hour|day>` with an ASCII positive integer.
- `key(head)` returns the identity (`str`), or `None`/empty for `"unknown"`.
  The default `client_ip` is the direct peer address: forwarded headers are not
  trusted and IPv6 addresses are not aggregated by prefix. Provide your own
  `key` for trusted proxies, IPv6 networks or authenticated identities. A key
  callback returning a non-`str` raises `TypeError`; one that raises results in
  the normal 500, never in bypassing the limit.
- Identities longer than 128 characters are replaced by a 16-byte BLAKE2b
  digest.

## Behaviour

- The quota is taken before the handler, so 4xx/5xx responses count too.
  Rejected requests are not recorded and do not extend the window.
- Successful responses carry `X-RateLimit-Limit` and `X-RateLimit-Remaining`
  (after this request). Over the quota: `429` problem+json with `Retry-After`
  (seconds, rounded up, at least 1) and the same two headers with `Remaining: 0`.
- At most `max_keys` identities are tracked. Active buckets are never evicted:
  when full and nothing can be reclaimed, new identities get `503` with
  `Retry-After`, while tracked identities stay limited.
- Memory is bounded by `max_keys` times the limit in timestamps. Both are
  trusted deployment settings.
- One factory call is one shared quota across its routes; call it again for an
  independent quota. `app.use` also covers 404/405; groups only cover their own
  routes; WebSockets are not limited. When limiters are stacked, all apply, and
  the outermost one's `X-RateLimit-*` values are the ones the client sees.
- The rejection is returned directly, so custom error handlers do not alter it.

## Limits

- State is per process: not shared across workers, lost on restart. It is not a
  global quota.
- The default key is not a defence against changing IPs or botnets.
- The middleware does not add `429` to the OpenAPI document and defines no
  authentication guard or security scheme.
