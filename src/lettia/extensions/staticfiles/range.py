"""HTTP Range header parsing and byte-range specifications (RFC 7233)."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ByteRange:
    """A resolved, inclusive byte range [start, end] for a file of total_size."""

    start: int
    end: int
    total_size: int

    @property
    def length(self) -> int:
        """The total number of bytes in this range."""
        return self.end - self.start + 1

    @property
    def content_range(self) -> str:
        """The value for the Content-Range response header."""
        return f"bytes {self.start}-{self.end}/{self.total_size}"


type RangeResult = ByteRange | Literal["unsatisfiable", "ignore"]


def parse_range_header(header_value: str | None, total_size: int) -> RangeResult:
    """Parse a single Range header value (RFC 7233).

    Returns:
    - ``ByteRange``: valid satisfiable single-range request.
    - ``"unsatisfiable"``: range cannot be satisfied (emit HTTP 416).
    - ``"ignore"``: absent, non-byte unit, multi-range, or malformed syntax.
    """
    if not header_value:
        return "ignore"

    raw = header_value.strip()
    if not raw.startswith("bytes="):
        return "ignore"

    spec = raw[len("bytes=") :].strip()
    # Multipart ranges are not supported; RFC 7233 allows ignoring and returning 200.
    if "," in spec:
        return "ignore"

    if "-" not in spec:
        return "ignore"

    start_str, end_str = spec.split("-", 1)
    start_str = start_str.strip()
    end_str = end_str.strip()

    if not start_str and not end_str:
        return "ignore"

    try:
        start = int(start_str) if start_str else None
        end = int(end_str) if end_str else None
    except ValueError:
        return "ignore"

    if start is None:
        if end is None:
            return "ignore"
        if end <= 0 or total_size <= 0:
            return "unsatisfiable"
        return ByteRange(
            start=max(0, total_size - end),
            end=total_size - 1,
            total_size=total_size,
        )

    if start < 0 or start >= total_size:
        return "unsatisfiable"

    if end is None:
        return ByteRange(start=start, end=total_size - 1, total_size=total_size)

    if end < start:
        return "unsatisfiable"

    return ByteRange(start=start, end=min(end, total_size - 1), total_size=total_size)
