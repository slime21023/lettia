import pytest
from hypothesis import given
from hypothesis import strategies as st

from lettia.middleware import session
from lettia.middleware.session import _sign, _unsign


@pytest.mark.contract("MW-SESSION")
def test_signed_session_round_trip_allows_dots_in_payload() -> None:
    payload = b'{"user_id":"user.999"}'
    signed = _sign(payload, b"secret")

    assert _unsign(signed, b"secret") == payload


@pytest.mark.contract("MW-SESSION")
@given(lifetime=st.integers(max_value=0))
def test_session_rejects_nonpositive_lifetime(lifetime: int) -> None:
    with pytest.raises(ValueError):
        session("test-secret", max_age=lifetime)
