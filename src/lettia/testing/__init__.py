"""Tools for testing code built on lettia.

``make_context`` needs nothing beyond lettia. ``TestClient`` needs ``httpx``
(``pip install 'lettia[test]'``) and is only imported when it is used.
"""

from typing import TYPE_CHECKING, Any

from lettia.testing.context import make_context

if TYPE_CHECKING:
    from lettia.testing.client import TestClient

__all__ = ["TestClient", "make_context"]


def __getattr__(name: str) -> Any:
    if name == "TestClient":
        from lettia.testing.client import TestClient

        return TestClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
