"""Case-insensitive, multi-valued HTTP headers."""

import re
from collections.abc import Iterable, Iterator, Mapping
from functools import lru_cache
from typing import overload, override

type RawHeaders = list[tuple[bytes, bytes]]

_TOKEN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")


@lru_cache(maxsize=512)
def _key(name: str) -> bytes:
    return name.lower().encode("latin-1")


class Headers(Mapping[str, str]):
    """Read-only view over raw ASGI headers.

    ``raw`` holds lowercase names, as the ASGI spec requires of servers. Lookup
    scans the list: requests carry few headers, so building a dict up front would
    cost more than it saves.
    """

    __slots__ = ("_raw",)

    def __init__(self, raw: RawHeaders) -> None:
        self._raw = raw

    @classmethod
    def from_pairs(cls, pairs: Iterable[tuple[str, str]]) -> "Headers":
        return cls([(_key(n), v.encode("latin-1")) for n, v in pairs])

    def __getitem__(self, name: str) -> str:
        key = _key(name)
        for raw_name, raw_value in self._raw:
            if raw_name == key:
                return raw_value.decode("latin-1")
        raise KeyError(name)

    @overload
    def get(self, name: str, /) -> str | None: ...
    @overload
    def get[T](self, name: str, default: str | T, /) -> str | T: ...
    @override
    def get[T](self, name: str, default: str | T | None = None, /) -> str | T | None:
        key = _key(name)
        for raw_name, raw_value in self._raw:
            if raw_name == key:
                return raw_value.decode("latin-1")
        return default

    def get_all(self, name: str) -> list[str]:
        key = _key(name)
        return [v.decode("latin-1") for n, v in self._raw if n == key]

    def multi_items(self) -> list[tuple[str, str]]:
        return [(n.decode("latin-1"), v.decode("latin-1")) for n, v in self._raw]

    @override
    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        key = _key(name)
        return any(raw_name == key for raw_name, _ in self._raw)

    def __iter__(self) -> Iterator[str]:
        seen: set[bytes] = set()
        for raw_name, _ in self._raw:
            if raw_name not in seen:
                seen.add(raw_name)
                yield raw_name.decode("latin-1")

    def __len__(self) -> int:
        return len({raw_name for raw_name, _ in self._raw})


class MutableHeaders(Headers):
    """Headers of a response; values are validated when they are written."""

    __slots__ = ()

    def __init__(self, raw: RawHeaders | None = None) -> None:
        super().__init__([] if raw is None else raw)

    @property
    def raw(self) -> RawHeaders:
        """The ``(name, value)`` byte pairs the ASGI adapter sends."""
        return self._raw

    def set(self, name: str, value: str) -> None:
        """Replace every existing value of ``name``."""
        pair = self._encode(name, value)
        self._drop(pair[0])
        self._raw.append(pair)

    def add(self, name: str, value: str) -> None:
        """Append a value, keeping existing ones (for example ``set-cookie``)."""
        self._raw.append(self._encode(name, value))

    def add_known(self, name: bytes, value: str) -> None:
        """Append a header whose lowercase ``name`` the caller already validated.

        For names fixed when a route is compiled: only the value is checked.
        """
        self._raw.append((name, self._encode_value(value)))

    def remove(self, name: str) -> None:
        self._drop(_key(name))

    def _drop(self, key: bytes) -> None:
        self._raw[:] = [pair for pair in self._raw if pair[0] != key]

    @staticmethod
    def validated_name(name: str) -> bytes:
        """The lowercase bytes of a header name, for use with :meth:`add_known`."""
        if not _TOKEN.fullmatch(name):
            raise ValueError(f"invalid header name: {name!r}")
        return _key(name)

    @staticmethod
    def _encode_value(value: str) -> bytes:
        if "\r" in value or "\n" in value:
            raise ValueError("header value must not contain a line break")
        return value.encode("latin-1")

    @classmethod
    def _encode(cls, name: str, value: str) -> tuple[bytes, bytes]:
        return cls.validated_name(name), cls._encode_value(value)
