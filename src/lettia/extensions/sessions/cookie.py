"""Signed cookie token format and ``Set-Cookie`` rendering.

Token: ``v1.<payload>.<mac>`` with unpadded base64url segments. The payload is
JSON and is only decoded after its HMAC-SHA256 verified. It is signed, not
encrypted: anyone holding the cookie can read it.
"""

import base64
import binascii
import hashlib
import hmac
import re
import time
from collections.abc import Sequence
from email.utils import formatdate
from http.cookies import CookieError, SimpleCookie
from typing import Any, Literal, NamedTuple

import msgspec

from lettia.extensions.sessions.session import (
    SessionValue,
    validate_data,
    validate_flash,
)

VERSION = "v1"
CLOCK_SKEW_SEC = 60
_MAC_CONTEXT = b"lettia.session.v1\0"
_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")
_TOKEN_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_RESERVED_NAMES = frozenset(
    {
        "comment",
        "domain",
        "expires",
        "httponly",
        "max-age",
        "partitioned",
        "path",
        "samesite",
        "secure",
        "version",
    }
)

type SameSite = Literal["lax", "strict", "none"]


class _Payload(msgspec.Struct, forbid_unknown_fields=True):
    data: dict[str, Any]
    flash: list[tuple[str, str]]
    iat: int
    exp: int


class Decoded(NamedTuple):
    data: dict[str, SessionValue]
    flash: list[tuple[str, str]]
    iat: int
    exp: int
    key_index: int


_decode_payload = msgspec.json.Decoder(_Payload).decode
_encode_json = msgspec.json.Encoder().encode
_encode_canonical = msgspec.json.Encoder(order="sorted").encode


def _now() -> int:
    return int(time.time())


def current_time() -> int:
    """The clock the middleware reads, in Unix seconds."""
    return _now()


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64_decode(segment: str) -> bytes | None:
    """Decode a canonical unpadded base64url segment, or ``None``."""
    if not _SEGMENT.fullmatch(segment) or len(segment) % 4 == 1:
        return None
    try:
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError):
        return None
    return raw if _b64_encode(raw) == segment else None


def _mac(key: bytes, name: str, payload_segment: str) -> bytes:
    message = _MAC_CONTEXT + name.encode("ascii") + b"\0" + payload_segment.encode()
    return hmac.new(key, message, hashlib.sha256).digest()


def canonical_state(data: object, flash: object) -> bytes:
    """Validated canonical JSON of the state, to detect changes by value."""
    validate_data(data)
    validate_flash(flash)
    try:
        return _encode_canonical([data, flash])
    except (msgspec.EncodeError, TypeError, ValueError) as exc:
        raise ValueError("session state cannot be encoded as JSON") from exc


def encode_token(
    key: bytes,
    name: str,
    data: dict[str, SessionValue],
    flash: list[tuple[str, str]],
    iat: int,
    exp: int,
) -> str:
    validate_data(data)
    validate_flash(flash)
    try:
        payload = _encode_json(_Payload(data, flash, iat, exp))
    except (msgspec.EncodeError, TypeError, ValueError) as exc:
        raise ValueError("session state cannot be encoded as JSON") from exc
    segment = _b64_encode(payload)
    return f"{VERSION}.{segment}.{_b64_encode(_mac(key, name, segment))}"


def decode_token(
    token: str,
    name: str,
    keys: Sequence[bytes],
    *,
    max_age: int,
    max_cookie_bytes: int,
    now: int,
) -> Decoded | None:
    """The verified, valid state in ``token``, or ``None`` for any defect."""
    if len(token) > max_cookie_bytes:
        return None
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != VERSION:
        return None
    _, payload_segment, mac_segment = parts
    mac = _b64_decode(mac_segment)
    if mac is None or len(mac) != 32 or not _SEGMENT.fullmatch(payload_segment):
        return None
    index: int | None = None
    for i, key in enumerate(keys):  # every key is tried: no early exit
        if hmac.compare_digest(_mac(key, name, payload_segment), mac) and index is None:
            index = i
    if index is None:
        return None
    raw = _b64_decode(payload_segment)
    if raw is None:
        return None
    try:
        payload = _decode_payload(raw)
        validate_data(payload.data)
        validate_flash(payload.flash)
    except (msgspec.DecodeError, ValueError, RecursionError, OverflowError):
        return None
    iat, exp = payload.iat, payload.exp
    if not (
        exp > iat >= 0
        and iat <= now + CLOCK_SKEW_SEC
        and now < exp
        and exp - iat <= max_age
    ):
        return None
    return Decoded(payload.data, payload.flash, iat, exp, index)


def validate_name(name: str, *, secure: bool, path: str) -> None:
    if type(name) is not str or not _TOKEN_NAME.fullmatch(name):
        raise ValueError(f"invalid cookie name: {name!r}")
    if name.lower() in _RESERVED_NAMES:
        raise ValueError(f"cookie name {name!r} is a reserved attribute name")
    lowered = name.lower()
    if lowered.startswith("__secure-") and not secure:
        raise ValueError("__Secure- cookies require secure=True")
    if lowered.startswith("__host-") and not (secure and path == "/"):
        raise ValueError("__Host- cookies require secure=True and path='/'")
    try:
        SimpleCookie()[name] = "x"
    except CookieError as exc:
        raise ValueError(f"invalid cookie name: {name!r}") from exc


def validate_path(path: str) -> None:
    if (
        type(path) is not str
        or not path.startswith("/")
        or ";" in path
        or any(not " " <= ch < "\x7f" for ch in path)
    ):
        raise ValueError(f"invalid cookie path: {path!r}")


def render(
    name: str,
    value: str,
    *,
    path: str,
    same_site: SameSite,
    secure: bool,
    max_age: int,
    expires: int,
) -> str:
    """One ``Set-Cookie`` value with HttpOnly and no Domain."""
    jar: SimpleCookie = SimpleCookie()
    try:
        jar[name] = value
        morsel = jar[name]
        morsel["path"] = path
        morsel["httponly"] = True
        if secure:
            morsel["secure"] = True
        morsel["samesite"] = same_site.capitalize()
        morsel["max-age"] = str(max_age)
        morsel["expires"] = formatdate(expires, usegmt=True)
    except CookieError as exc:
        raise ValueError(f"cannot render cookie: {exc}") from exc
    return morsel.OutputString()
