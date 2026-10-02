from urllib.parse import unquote, urlsplit

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from strategies import SEGMENTS, TEXT

from lettia.router import Router


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
