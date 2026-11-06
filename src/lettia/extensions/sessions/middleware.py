"""Middleware that loads the session from a signed cookie and commits changes."""

from collections.abc import Sequence

from lettia.core import Context, Handler, Response
from lettia.extensions.sessions import cookie
from lettia.extensions.sessions.cookie import SameSite
from lettia.extensions.sessions.session import SESSION_KEY, Session, SessionValue

_MIN_KEY_BYTES = 32
_SAME_SITE = ("lax", "strict", "none")


def _positive_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive int")
    return value


def _cookie_values(c: Context, name: str) -> tuple[int, str]:
    """How often ``name`` appears over all Cookie headers, and the last value."""
    count = 0
    value = ""
    for header in c.request.headers.get_all("cookie"):
        for piece in header.split(";"):
            piece_name, _, piece_value = piece.partition("=")
            if piece_name.strip() == name:
                count += 1
                value = piece_value.strip()
    return count, value


class SessionMiddleware:
    """Signed-cookie sessions; the cookie is signed, not encrypted.

    The first key signs, every key verifies, so keys can rotate. There is no
    server-side storage: logging out only deletes the browser's cookie, and an
    old token stays replayable until it expires.
    """

    def __init__(
        self,
        secret_keys: Sequence[bytes],
        *,
        cookie_name: str = "lettia_session",
        max_age: int = 1_209_600,
        same_site: SameSite = "lax",
        secure: bool = True,
        path: str = "/",
        max_cookie_bytes: int = 4096,
    ) -> None:
        if isinstance(secret_keys, (str, bytes, bytearray, memoryview)):
            raise ValueError("secret_keys must be a sequence of bytes keys")
        keys = tuple(secret_keys)
        if not keys:
            raise ValueError("secret_keys must not be empty")
        for key in keys:
            if type(key) is not bytes or len(key) < _MIN_KEY_BYTES:
                raise ValueError(
                    f"every secret key must be bytes of >= {_MIN_KEY_BYTES}"
                )
        self._keys = keys
        self._max_age = _positive_int("max_age", max_age)
        self._max_cookie_bytes = _positive_int("max_cookie_bytes", max_cookie_bytes)
        if self._max_cookie_bytes > 4096:
            raise ValueError("max_cookie_bytes must be in 1..4096")
        if same_site not in _SAME_SITE:
            raise ValueError(f"same_site must be one of {_SAME_SITE}")
        if same_site == "none" and not secure:
            raise ValueError("same_site='none' requires secure=True")
        cookie.validate_path(path)
        cookie.validate_name(cookie_name, secure=secure, path=path)
        self._name = cookie_name
        self._same_site: SameSite = same_site
        self._secure = secure
        self._path = path
        now = cookie.current_time()
        # A configuration that cannot hold even an empty session would fail
        # every write and every delete: refuse it now.
        probe = self._signed({}, [], now, now + self._max_age, now)
        self._check_size(probe)
        self._check_size(self._deletion())

    def _signed(
        self,
        data: dict[str, SessionValue],
        flash: list[tuple[str, str]],
        iat: int,
        exp: int,
        now: int,
    ) -> str:
        token = cookie.encode_token(self._keys[0], self._name, data, flash, iat, exp)
        return cookie.render(
            self._name,
            token,
            path=self._path,
            same_site=self._same_site,
            secure=self._secure,
            max_age=exp - now,
            expires=exp,
        )

    def _deletion(self) -> str:
        return cookie.render(
            self._name,
            "",
            path=self._path,
            same_site=self._same_site,
            secure=self._secure,
            max_age=0,
            expires=0,
        )

    def _check_size(self, header: str) -> None:
        if len(header) > self._max_cookie_bytes:
            raise ValueError(
                f"session cookie is {len(header)} bytes, over the limit of "
                f"{self._max_cookie_bytes}"
            )

    def __call__(self, next_handler: Handler) -> Handler:
        async def run(c: Context) -> Response:
            now = cookie.current_time()
            count, raw = _cookie_values(c, self._name)
            loaded = None
            if count == 1 and not raw.startswith('"'):
                loaded = cookie.decode_token(
                    raw,
                    self._name,
                    self._keys,
                    max_age=self._max_age,
                    max_cookie_bytes=self._max_cookie_bytes,
                    now=now,
                )
            if loaded is None:
                session = Session()
            else:
                session = Session(loaded.data, list(loaded.flash))
            data, flash = session.export()
            before = cookie.canonical_state(data, flash)
            SESSION_KEY.set(c, session)

            response = await next_handler(c)
            if response.status >= 500:
                return response
            try:
                self._commit(response, session, before, loaded, count > 0)
            except Exception as exc:
                response.after_send = None
                await _close_body(response, exc)
                raise
            return response

        return run

    def _commit(
        self,
        response: Response,
        session: Session,
        before: bytes,
        loaded: cookie.Decoded | None,
        had_cookie: bool,
    ) -> None:
        now = cookie.current_time()
        data, flash = session.export()
        after = cookie.canonical_state(data, flash)
        empty = not data and not flash
        header: str | None
        if loaded is not None:
            if now >= loaded.exp:
                header = self._deletion()
            elif after == before and loaded.key_index == 0:
                return
            elif empty:
                header = self._deletion()
            else:
                header = self._signed(data, flash, loaded.iat, loaded.exp, now)
        elif empty:
            if not had_cookie:
                return
            header = self._deletion()
        else:
            header = self._signed(data, flash, now, now + self._max_age, now)
        self._check_size(header)
        response.headers.add("set-cookie", header)


async def _close_body(response: Response, error: Exception) -> None:
    aclose = getattr(response.body, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception as close_error:
        raise error from close_error
