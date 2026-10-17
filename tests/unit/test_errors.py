from collections.abc import Callable

import pytest

from lettia.core import ClientDisconnect, Forbidden, HTTPError, Unauthorized


def test_http_error_carries_status_title_and_optional_detail() -> None:
    err = HTTPError(404, "Not Found", "no such user")

    assert (err.status, err.title, err.detail) == (404, "Not Found", "no such user")


def test_http_error_detail_defaults_to_none() -> None:
    assert HTTPError(400, "Bad Request").detail is None


def test_http_error_str_is_status_and_title() -> None:
    assert str(HTTPError(413, "Payload Too Large")) == "413 Payload Too Large"


@pytest.mark.parametrize("status", [99, 200, 399, 600])
def test_http_error_outside_error_range_is_rejected(status: int) -> None:
    with pytest.raises(ValueError, match="status"):
        HTTPError(status, "x")


@pytest.mark.parametrize(
    ("cls", "status", "title"),
    [(Unauthorized, 401, "Unauthorized"), (Forbidden, 403, "Forbidden")],
)
def test_guard_errors_are_http_errors_with_fixed_status(
    cls: Callable[[], HTTPError], status: int, title: str
) -> None:
    err = cls()

    assert isinstance(err, HTTPError)
    assert (err.status, err.title, err.detail) == (status, title, None)


def test_guard_error_accepts_a_detail() -> None:
    assert Forbidden("admin only").detail == "admin only"


def test_client_disconnect_is_not_an_http_error() -> None:
    assert not issubclass(ClientDisconnect, HTTPError)
    assert issubclass(ClientDisconnect, Exception)
