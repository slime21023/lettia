import pytest

from lettia.response import Response
from tests.support.asgi import http_context


@pytest.mark.contract("RESP-POLICY")
def test_policy_activation_is_idempotent_and_registration_preserves_order() -> None:
    ctx = http_context()
    response = Response()
    calls: list[str] = []
    ctx._register_response_finalizer(response, lambda r: calls.append("immediate"))
    assert calls == ["immediate"]
    ctx._defer_response_policies()
    ctx._register_response_finalizer(response, lambda r: calls.append("first"))
    ctx._defer_response_policies()
    ctx._register_response_finalizer(response, lambda r: calls.append("second"))
    assert calls == ["immediate"]
    ctx._finalize_response(response)
    assert calls == ["immediate", "first", "second"]


@pytest.mark.contract("RESP-POLICY")
def test_policy_failure_removes_only_failed_callback_and_preserves_latest_state() -> (
    None
):
    ctx = http_context()
    ctx._defer_response_policies()
    calls: list[str] = []
    latest = ["before"]

    def broken(response: Response) -> None:
        calls.append("broken")
        raise ValueError("policy failed")

    def valid(response: Response) -> None:
        calls.append("valid")
        response.set_header("x-state", latest[0])

    response = Response()
    ctx._register_response_finalizer(response, broken)
    ctx._register_response_finalizer(response, valid)
    with pytest.raises(ValueError, match="policy failed"):
        ctx._finalize_response(response)
    assert calls == ["broken", "valid"]
    latest[0] = "after"
    fallback = Response()
    ctx._finalize_response(fallback)
    assert calls == ["broken", "valid", "valid"]
    assert fallback.headers == {"x-state": "after"}
