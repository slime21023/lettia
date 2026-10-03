import inspect
from typing import get_type_hints

import pytest

from lettia import App
from lettia.protocols import AttrsBinder, Binder, DataclassBinder, PydanticBinder
from lettia.response import Response, ResponseWriter


@pytest.mark.contract("APP-API")
def test_app_has_no_application_state() -> None:
    app = App()

    assert not hasattr(app, "state")
    assert not hasattr(app, "router")
    assert not hasattr(app, "global_middlewares")
    assert not hasattr(app, "on_startup")
    with pytest.raises(AttributeError):
        object.__setattr__(app, "state", {})


@pytest.mark.contract("APP-API")
def test_refactored_boundaries_keep_public_signatures_and_header_type() -> None:
    assert list(inspect.signature(ResponseWriter).parameters) == ["send", "head_only"]
    write = inspect.signature(ResponseWriter.write)
    assert list(write.parameters) == ["self", "response", "deadline"]
    assert write.parameters["deadline"].default is None
    assert get_type_hints(ResponseWriter.write)["return"] is type(None)
    assert get_type_hints(Response)["headers"] == dict[str, str]
    for binder in (Binder, AttrsBinder, DataclassBinder, PydanticBinder):
        assert list(inspect.signature(binder.bind).parameters) == [
            "self",
            "ctx",
            "target_type",
        ]
