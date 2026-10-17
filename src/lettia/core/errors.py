"""Errors raised by the core layer and converted centrally by ``App``."""

from collections.abc import Mapping
from typing import override


class HTTPError(Exception):
    """An error with an HTTP status; rendered as RFC 9457 problem+json."""

    def __init__(
        self,
        status: int,
        title: str,
        detail: str | None = None,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if not 400 <= status <= 599:
            raise ValueError(f"status must be in 400..599, got {status}")
        super().__init__(status, title, detail)
        self.status = status
        self.title = title
        self.detail = detail
        self.headers: dict[str, str] = {} if headers is None else dict(headers)

    @override
    def __str__(self) -> str:
        return f"{self.status} {self.title}"


class Unauthorized(HTTPError):
    """Raised by a guard when the caller is not authenticated (401)."""

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(401, "Unauthorized", detail)


class Forbidden(HTTPError):
    """Raised by a guard when the caller is authenticated but not allowed (403)."""

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(403, "Forbidden", detail)


class ClientDisconnect(Exception):
    """The client went away while the request body was being read."""


class StartupError(Exception):
    """One or more startup checks failed; every problem is listed."""

    def __init__(self, problems: list[str]) -> None:
        listing = "\n".join(f"- {problem}" for problem in problems)
        super().__init__(f"startup checks failed:\n{listing}")
        self.problems = problems
