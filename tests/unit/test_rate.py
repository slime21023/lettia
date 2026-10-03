from importlib import import_module
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lettia.middleware import MemoryRateLimiter

rate_limit_module = import_module("lettia.middleware.rate_limit")


@pytest.mark.contract("MW-RATE")
def test_rate_limiter_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        MemoryRateLimiter(requests_per_minute=0)


@pytest.mark.contract("MW-RATE")
def test_rate_limiter_purges_inactive_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = 0.0
    monkeypatch.setattr(rate_limit_module.time, "monotonic", lambda: clock)
    limiter = MemoryRateLimiter(requests_per_minute=10)
    limiter.is_allowed("inactive")

    clock = 61.0
    for index in range(255):
        limiter.is_allowed(f"active-{index}")

    assert "inactive" not in limiter._history
    assert "active-254" in limiter._history


@pytest.mark.contract("MW-RATE")
@given(
    limit=st.integers(1, 10),
    operations=st.lists(
        st.tuples(
            st.integers(0, 2), st.sampled_from([0.0, 0.5, 1.0, 59.5, 60.0, 61.0])
        ),
        min_size=1,
        max_size=50,
    ),
)
def test_rate_limit_matches_independent_window_model(
    limit: int,
    operations: list[tuple[int, float]],
) -> None:
    limiter = MemoryRateLimiter(limit)
    other = MemoryRateLimiter(limit)
    now = 100.0
    accepted: list[tuple[int, float]] = []
    for index, (key, advance) in enumerate(operations):
        now += advance
        active = [at for owner, at in accepted if owner == key and at > now - 60]
        expected = len(active) < limit
        with patch.object(rate_limit_module.time, "monotonic", return_value=now):
            allowed, retry = limiter.is_allowed(str(key))
            assert allowed == expected
            if allowed:
                accepted.append((key, now))
                assert retry == 0
            else:
                assert retry >= 1
            assert other.is_allowed(str(index))[0]
