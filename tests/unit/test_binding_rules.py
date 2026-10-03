import dataclasses

import pytest
from attrs import define, field
from hypothesis import given
from hypothesis import strategies as st

from lettia._binding import bound_kwargs, coerce_type, scalar_type
from lettia.asgi import JSONValue


@pytest.mark.contract("BIND-CONSTRUCT", "BIND-SCALAR", "BIND-SCHEMA")
def test_model_rules_respect_constructor_names_and_initvar() -> None:
    from lettia._binding import (
        attrs_annotations,
        bound_kwargs,
        construct,
        dataclass_annotations,
        scalar_type,
    )

    @define
    class AttrModel:
        _value: int = field(alias="number")
        ignored: int = field(default=7, init=False)

    @dataclasses.dataclass
    class DataModel:
        value: int
        extra: dataclasses.InitVar[int] = 0
        ignored: int = dataclasses.field(default=7, init=False)

    assert attrs_annotations(AttrModel) == {"number": int}
    assert dataclass_annotations(DataModel) == {"value": int, "extra": int}
    kwargs = bound_kwargs(
        {"number": "42", "ignored": "9"}, {"number": scalar_type(int)}
    )
    assert construct(AttrModel, kwargs)._value == 42
    with pytest.raises(TypeError, match="Unsupported"):
        scalar_type(list[int])
    with pytest.raises(ValueError, match="float range"):
        bound_kwargs({"n": 10**1000}, {"n": scalar_type(float)})


@pytest.mark.contract("BIND-SCALAR")
@given(
    value=st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.text(),
        st.floats(allow_nan=False, allow_infinity=False),
    ),
)
def test_scalar_rules_preserve_matching_values_and_nullable_none(
    value: JSONValue,
) -> None:
    target = type(value) if value is not None else str
    assert coerce_type(value, target, True) == value
    assert type(coerce_type(value, target, True)) is type(value)


@pytest.mark.contract("BIND-SCALAR")
@pytest.mark.parametrize(
    "target,value,expected",
    [
        (bool, "YeS", True),
        (bool, "false", False),
        (bool, "0", False),
        (int, "-17", -17),
        (float, "1.5", 1.5),
        (float, 3, 3.0),
    ],
)
def test_scalar_text_conversion_uses_typed_values(
    target: type[object],
    value: JSONValue,
    expected: object,
) -> None:
    actual = bound_kwargs({"value": value}, {"value": scalar_type(target)})["value"]
    assert actual == expected and type(actual) is type(expected)


@pytest.mark.contract("BIND-SCALAR")
@pytest.mark.parametrize(
    "target,value",
    [
        (str, 1),
        (str, None),
        (int, True),
        (int, 1.5),
        (int, "bad"),
        (float, True),
        (float, []),
        (float, "bad"),
        (bool, 1),
        (bool, "perhaps"),
    ],
)
def test_scalar_input_errors_stay_value_errors(
    target: type[object], value: JSONValue
) -> None:
    with pytest.raises(ValueError):
        coerce_type(value, *scalar_type(target))


@pytest.mark.contract("BIND-SCHEMA")
@pytest.mark.parametrize("annotation", [list[int], int | str, object])
def test_unsupported_scalar_schema_stays_configuration_error(
    annotation: object,
) -> None:
    with pytest.raises(TypeError, match="Unsupported"):
        scalar_type(annotation)
