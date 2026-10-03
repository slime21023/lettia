from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lettia.errors import HTTPException
from lettia.protocols import (
    CallableValidator,
)


@pytest.mark.contract("VALIDATE-PREDICATE")
@given(age=st.integers(-100, 150))
def test_callable_validator_enforces_predicate(age: int) -> None:
    validator = CallableValidator[int](lambda value: value >= 18)
    if age >= 18:
        validator.validate(age)
    else:
        with pytest.raises(HTTPException) as error:
            validator.validate(age)
        assert error.value.status_code == 400
