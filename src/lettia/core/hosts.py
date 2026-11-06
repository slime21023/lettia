"""The ``Host`` header: how it is read, and the allowed-hosts policy."""

import re
from collections.abc import Sequence

_LABEL = r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?"
_NAME = rf"{_LABEL}(?:\.{_LABEL})*"
_IPV6 = r"\[[0-9a-f:.]{2,45}\]"
_HOST = re.compile(rf"({_NAME}|{_IPV6})(?::([0-9]{{1,5}}))?")
_NAME_ONLY = re.compile(_NAME)
_DEFAULT_PORTS = {"http": 80, "ws": 80, "https": 443, "wss": 443}


def parse_host(value: str | None) -> str | None:
    """A ``Host`` header value in lower case, or ``None`` if it is not one.

    Accepts a host name, an IPv4 address or a bracketed IPv6 address, with an
    optional port; anything else (spaces, ``/``, ``@``, a userinfo part, a
    missing name, a port above 65535) is rejected.
    """
    if value is None:
        return None
    host = value.lower()
    match = _HOST.fullmatch(host)
    if match is None:
        return None
    port = match.group(2)
    if port is not None and int(port) > 65535:
        return None
    return host


def hostname_of(host: str) -> str:
    """``host`` without its port; an IPv6 address keeps its brackets."""
    if host.startswith("["):
        return host[: host.index("]") + 1]
    return host.partition(":")[0]


def server_authority(server: tuple[str, int | None] | None, scheme: str) -> str:
    """The address the server listens on, written as a ``Host`` value.

    A port that is the default of ``scheme`` is left out. Without a usable
    address (no ``server`` in the scope, or a Unix socket) it is ``localhost``.
    """
    try:
        name, port = server or (None, None)
    except (TypeError, ValueError):  # not a (host, port) pair
        return "localhost"
    if port is None or not isinstance(name, str) or not name or "/" in name:
        return "localhost"
    name = name.lower()
    if ":" in name:
        name = f"[{name}]"
    if _DEFAULT_PORTS.get(scheme) == port:
        return name
    return f"{name}:{port}"


class HostPolicy:
    """The host names an application answers to.

    Patterns are exact names (``api.example.com``, ``[::1]``), ``*.example.com``
    for any subdomain (the bare domain is not matched) or ``*`` for every host.
    A port is never part of a pattern and is ignored when matching.
    """

    __slots__ = ("_allow_all", "_exact", "_suffixes")

    def __init__(self, patterns: Sequence[str]) -> None:
        if isinstance(patterns, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError("allowed_hosts must be a sequence of host patterns")
        exact: set[str] = set()
        suffixes: list[str] = []
        allow_all = False
        for pattern in patterns:
            lowered = pattern.lower()
            if lowered == "*":
                allow_all = True
            elif lowered.startswith("*."):
                if _NAME_ONLY.fullmatch(lowered[2:]) is None:
                    raise ValueError(f"invalid allowed host pattern: {pattern!r}")
                suffixes.append(lowered[1:])
            elif _HOST.fullmatch(lowered) is None or hostname_of(lowered) != lowered:
                raise ValueError(
                    f"invalid allowed host pattern: {pattern!r} "
                    "(a host name or address, without a port)"
                )
            else:
                exact.add(lowered)
        if not (allow_all or exact or suffixes):
            raise ValueError("allowed_hosts must name at least one host")
        self._allow_all = allow_all
        self._exact = frozenset(exact)
        self._suffixes = tuple(suffixes)

    def allows(self, host: str) -> bool:
        """Whether a (lower case) ``Host`` value names an allowed host."""
        if self._allow_all:
            return True
        name = hostname_of(host)
        if name in self._exact:
            return True
        return any(name.endswith(suffix) for suffix in self._suffixes)
