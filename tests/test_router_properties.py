import string
from urllib.parse import quote

from hypothesis import given
from hypothesis import strategies as st

from lettia.router import Router

SAFE_SEGMENTS = st.text(
    alphabet=string.ascii_letters + string.digits + "_-", min_size=1, max_size=20
)
URL_VALUES = st.text(
    alphabet=string.ascii_letters + string.digits + " _-./", min_size=1, max_size=20
)


@given(SAFE_SEGMENTS)
def test_router_static_route_precedes_parameter_route(value: str) -> None:
    router = Router()
    router.add_route("GET", "/users/me", "static")
    router.add_route("GET", "/users/:user_id", "parameter")

    route, params = router.match("GET", "/users/me") or (None, None)
    assert route is not None
    assert route.handler == "static"
    assert params == {}

    if value != "me":
        dynamic_route, dynamic_params = router.match("GET", f"/users/{value}") or (
            None,
            None,
        )
        assert dynamic_route is not None
        assert dynamic_route.handler == "parameter"
        assert dynamic_params == {"user_id": value}


@given(URL_VALUES)
def test_url_for_encodes_parameter_values(value: str) -> None:
    router = Router()
    router.add_route("GET", "/users/:user_id", "handler", name="user")

    assert router.url_for("user", user_id=value) == f"/users/{quote(value, safe='')}"
