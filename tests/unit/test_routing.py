from urllib.parse import unquote, urlsplit

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from lettia.router import Router
from tests.support.strategies import SEGMENTS, TEXT


@pytest.mark.contract("ROUTE-MATCH")
def test_router_keeps_parameter_names_per_method() -> None:
    router: Router[str] = Router()
    router.add_route("GET", "/users/:id", "get-user")
    router.add_route("POST", "/users/:user_id", "create-user")
    result = router.match("POST", "/users/7")
    assert result is not None
    assert result[0].handler == "create-user"
    assert result[1] == {"user_id": "7"}


@pytest.mark.contract("ROUTE-MATCH")
def test_router_keeps_parameter_names_for_shared_prefix_routes() -> None:
    router: Router[str] = Router()
    router.add_route("GET", "/a/:first", "short")
    router.add_route("GET", "/a/:second/b", "long")

    result = router.match("GET", "/a/value")

    assert result is not None
    assert result[0].handler == "short"
    assert result[1] == {"first": "value"}


@pytest.mark.contract("ROUTE-MATCH")
def test_explicit_head_route_precedes_get_fallback() -> None:
    router: Router[str] = Router()
    router.add_route("GET", "/users/profile", "get-profile")
    router.add_route("HEAD", "/users/:user_id", "head-user")

    result = router.match("HEAD", "/users/profile")

    assert result is not None
    assert result[0].handler == "head-user"
    assert result[1] == {"user_id": "profile"}


@pytest.mark.contract("ROUTE-NAMES")
def test_url_for_validates_and_encodes_parameters() -> None:
    router: Router[str] = Router()
    router.add_route("GET", "/users/:id", "user", name="user")
    router.add_route("GET", "/files/*path", "file", name="file")
    assert router.url_for("user", id="a b") == "/users/a%20b"
    assert (
        router.url_for("file", path="docs/read me.txt") == "/files/docs/read%20me.txt"
    )
    with pytest.raises(KeyError, match="Missing route parameters"):
        router.url_for("user")
    with pytest.raises(TypeError, match="Unexpected route parameters"):
        router.url_for("user", id="1", extra="value")


@pytest.mark.contract("ROUTE-MATCH")
def test_static_routing() -> None:
    router = Router()
    router.add_route("GET", "/users", "get_users", name="get_users")
    router.add_route("POST", "/users", "create_user", name="create_user")

    res_get = router.match("GET", "/users")
    assert res_get is not None
    route, params = res_get
    assert route.handler == "get_users"
    assert params == {}

    res_post = router.match("POST", "/users")
    assert res_post is not None
    route_post, params_post = res_post
    assert route_post.handler == "create_user"

    assert router.match("GET", "/nonexistent") is None


@pytest.mark.contract("ROUTE-MATCH")
def test_param_routing() -> None:
    router = Router()
    router.add_route("GET", "/users/:id", "get_user_by_id", name="user_detail")
    router.add_route("GET", "/users/:id/posts/:post_id", "get_user_post")

    res = router.match("GET", "/users/123")
    assert res is not None
    route, params = res
    assert route.handler == "get_user_by_id"
    assert params == {"id": "123"}

    res_post = router.match("GET", "/users/456/posts/789")
    assert res_post is not None
    route_post, params_post = res_post
    assert route_post.handler == "get_user_post"
    assert params_post == {"id": "456", "post_id": "789"}


@pytest.mark.contract("ROUTE-MATCH")
def test_wildcard_routing() -> None:
    router = Router()
    router.add_route("GET", "/static/*filepath", "static_handler")

    res = router.match("GET", "/static/css/main.css")
    assert res is not None
    route, params = res
    assert route.handler == "static_handler"
    assert params == {"filepath": "css/main.css"}


@pytest.mark.contract("ROUTE-NAMES")
def test_url_for() -> None:
    router = Router()
    router.add_route("GET", "/users/:id", "get_user", name="user_detail")
    router.add_route("GET", "/files/*filepath", "get_file", name="file_download")

    url = router.url_for("user_detail", id=42)
    assert url == "/users/42"

    url_file = router.url_for("file_download", filepath="docs/readme.txt")
    assert url_file == "/files/docs/readme.txt"

    with pytest.raises(KeyError):
        router.url_for("non_existent_route")


@pytest.mark.contract("ROUTE-MATCH")
def test_root_wildcard_matches_empty_path() -> None:
    router = Router()
    router.add_route("GET", "/*filepath", "files")

    result = router.match("GET", "/")

    assert result is not None
    assert result[0].handler == "files"
    assert result[1] == {"filepath": ""}


@pytest.mark.contract("ROUTE-MATCH")
def test_duplicate_routes_are_rejected() -> None:
    router = Router()
    router.add_route("GET", "/items", "first")

    with pytest.raises(ValueError, match="already registered"):
        router.add_route("GET", "/items", "second")


@pytest.mark.contract("ROUTE-MATCH")
def test_duplicate_route_names_are_rejected() -> None:
    router = Router()
    router.add_route("GET", "/first", "first", name="item")

    with pytest.raises(ValueError, match="already registered"):
        router.add_route("GET", "/second", "second", name="item")


@pytest.mark.contract("ROUTE-MATCH")
@given(segment=SEGMENTS, order=st.permutations(("static", "param", "wildcard")))
def test_router_precedence_is_independent_of_registration_order(
    segment: str, order: list[str]
) -> None:
    router: Router[str] = Router()
    patterns = {
        "static": f"/files/{segment}",
        "param": "/files/:id",
        "wildcard": "/files/*rest",
    }
    for name in order:
        router.add_route("GET", patterns[name], name)
    result = router.match("GET", f"/files/{segment}")
    assert result is not None and result[0].handler == "static" and result[1] == {}
    result = router.match("GET", f"/files/{segment}x")
    assert result is not None and result[0].handler == "param"
    assert result[1] == {"id": segment + "x"}
    result = router.match("GET", f"/files/{segment}/child")
    assert result is not None and result[0].handler == "wildcard"
    assert result[1] == {"rest": segment + "/child"}


@pytest.mark.contract("ROUTE-NAMES")
@given(value=TEXT)
@example(value="a b/?#%")
def test_url_for_encodes_one_raw_segment(value: str) -> None:
    router: Router[str] = Router()
    router.add_route("GET", "/users/:id", "user", name="user")
    url = router.url_for("user", id=value)
    parts = urlsplit(url)
    assert not parts.query and not parts.fragment
    encoded = parts.path.removeprefix("/users/")
    assert "/" not in encoded
    assert unquote(encoded) == value


@pytest.mark.contract("ROUTE-NAMES")
@given(
    segments=st.lists(SEGMENTS, min_size=1, max_size=8),
    method=st.sampled_from(["GET", "POST", "PATCH"]),
)
def test_named_wildcard_round_trip_preserves_handler_and_parameters(
    segments: list[str], method: str
) -> None:
    router: Router[object] = Router()
    handler = object()
    router.add_route(method, "/files/*rest", handler, name="file")
    path = "/".join(segments)
    result = router.match(method.lower(), unquote(router.url_for("file", rest=path)))
    assert result is not None and result[0].handler is handler
    assert result[1] == {"rest": path}


@pytest.mark.contract("ROUTE-MATCH")
@given(segment=SEGMENTS, explicit=st.booleans())
def test_head_and_allowed_methods_follow_registered_representations(
    segment: str, explicit: bool
) -> None:
    router: Router[str] = Router()
    path = f"/items/{segment}"
    router.add_route("GET", path, "get")
    router.add_route("WEBSOCKET", path, "socket")
    if explicit:
        router.add_route("HEAD", "/items/:id", "head")
    result = router.match("HEAD", path)
    assert result is not None and result[0].handler == ("head" if explicit else "get")
    assert router.allowed_methods(path) == {"GET", "HEAD"}
    with pytest.raises(ValueError):
        router.add_route("GET", path, "duplicate")
