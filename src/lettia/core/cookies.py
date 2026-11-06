"""Request ``Cookie`` header parsing and ``Set-Cookie`` serialization."""

import re
from collections.abc import Iterable
from email.utils import formatdate
from typing import Literal

type SameSite = Literal["lax", "strict", "none"]

# Browsers drop a cookie whose name, value and attributes exceed this many bytes.
MAX_SET_COOKIE_BYTES = 4096

_TOKEN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*")
_DOMAIN = re.compile(r"\.?[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")
_SAME_SITE = ("lax", "strict", "none")
# A cookie with one of these names would read like an attribute of another.
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


def parse_cookies(header_values: Iterable[str]) -> dict[str, str]:
    """Parse ``Cookie`` headers; the first value of a repeated name wins.

    Pieces without a name or without ``=`` are ignored rather than rejected: a
    malformed cookie must not make the whole request fail.
    """
    cookies: dict[str, str] = {}
    for header in header_values:
        for piece in header.split(";"):
            name, separator, value = piece.partition("=")
            name = name.strip()
            if not separator or not name:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            cookies.setdefault(name, value)
    return cookies


def check_cookie_path(path: str) -> None:
    """Raise ``ValueError`` unless ``path`` is a plain absolute cookie path."""
    if (
        type(path) is not str
        or not path.startswith("/")
        or ";" in path
        or any(not " " <= char < "\x7f" for char in path)
    ):
        raise ValueError(f"invalid cookie path: {path!r}")


def check_cookie_name(
    name: str, *, secure: bool, path: str, domain: str | None = None
) -> None:
    """Raise ``ValueError`` for a name that cannot be used with these attributes."""
    if type(name) is not str or not _TOKEN.fullmatch(name):
        raise ValueError(f"invalid cookie name: {name!r}")
    lowered = name.lower()
    if lowered in _RESERVED_NAMES:
        raise ValueError(f"cookie name {name!r} is a reserved attribute name")
    if lowered.startswith("__secure-") and not secure:
        raise ValueError("__Secure- cookies require secure=True")
    if lowered.startswith("__host-") and not (
        secure and path == "/" and domain is None
    ):
        raise ValueError("__Host- cookies require secure=True, path='/' and no domain")


def format_set_cookie(
    name: str,
    value: str,
    *,
    max_age: int | None = None,
    expires: int | None = None,
    path: str = "/",
    domain: str | None = None,
    secure: bool = False,
    httponly: bool = True,
    samesite: SameSite | None = "lax",
) -> str:
    """One ``Set-Cookie`` header value; ``expires`` is Unix seconds.

    The value is written as given, so it must already be made of cookie-safe
    characters (no whitespace, quotes, commas, semicolons or backslashes):
    encode anything else first. Raises ``ValueError`` for an invalid part, for
    ``SameSite=None`` without ``Secure``, and for a result browsers would drop.
    """
    check_cookie_name(name, secure=secure, path=path, domain=domain)
    check_cookie_path(path)
    if type(value) is not str or not _VALUE.fullmatch(value):
        raise ValueError(
            "cookie value must not contain whitespace, quotes, commas, "
            "semicolons, backslashes or control characters"
        )
    if domain is not None and not (
        type(domain) is str and _DOMAIN.fullmatch(domain) and len(domain) <= 255
    ):
        raise ValueError(f"invalid cookie domain: {domain!r}")
    if samesite is not None and samesite not in _SAME_SITE:
        raise ValueError(f"samesite must be one of {_SAME_SITE}, got {samesite!r}")
    if samesite == "none" and not secure:
        raise ValueError("samesite='none' requires secure=True")
    parts = [f"{name}={value}"]
    if expires is not None:
        parts.append(f"Expires={formatdate(expires, usegmt=True)}")
    if max_age is not None:
        if isinstance(max_age, bool) or not isinstance(max_age, int):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("max_age must be an int")
        parts.append(f"Max-Age={max_age}")
    if domain is not None:
        parts.append(f"Domain={domain}")
    parts.append(f"Path={path}")
    if secure:
        parts.append("Secure")
    if httponly:
        parts.append("HttpOnly")
    if samesite is not None:
        parts.append(f"SameSite={samesite.capitalize()}")
    header = "; ".join(parts)
    if len(header) > MAX_SET_COOKIE_BYTES:
        raise ValueError(
            f"cookie is {len(header)} bytes, over the {MAX_SET_COOKIE_BYTES} "
            "browsers accept"
        )
    return header
