"""Multi-valued query parameters."""

from collections.abc import Iterator, Mapping
from typing import override
from urllib.parse import parse_qsl


class QueryParams(Mapping[str, str]):
    """Parsed ``form``-style query string; item access returns the first value."""

    __slots__ = ("_values",)

    def __init__(self, values: dict[str, list[str]]) -> None:
        self._values = values

    @classmethod
    def parse(cls, query_string: bytes) -> "QueryParams":
        values: dict[str, list[str]] = {}
        pairs = parse_qsl(
            query_string.decode("latin-1"), keep_blank_values=True, errors="replace"
        )
        for name, value in pairs:
            values.setdefault(name, []).append(value)
        return cls(values)

    @override
    def __getitem__(self, name: str) -> str:
        return self._values[name][0]

    def get_all(self, name: str) -> list[str]:
        return list(self._values.get(name, ()))

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    @override
    def __len__(self) -> int:
        return len(self._values)
