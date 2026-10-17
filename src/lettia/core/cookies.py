"""Request ``Cookie`` header parsing."""

from collections.abc import Iterable


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
