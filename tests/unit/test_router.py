import pytest

from lettia.core import Context, Handler, Response
from lettia.core.router import Matched, MethodNotAllowed, RouteConflict, Router


def handler(name: str) -> Handler:
    async def h(c: Context) -> Response:
        return c.text(200, name)

    h.__name__ = name
    return h


def match(router: Router, method: str, path: str) -> Matched | MethodNotAllowed | None:
    return router.match(method, path.encode("latin-1"))


def matched(router: Router, method: str, path: str) -> Matched:
    result = match(router, method, path)
    assert isinstance(result, Matched), result
    return result


# --- static routes ------------------------------------------------------


def test_router_matches_a_static_path_and_returns_its_handler() -> None:
    router, users = Router(), handler("users")
    router.add("GET", "/users", users)

    found = matched(router, "GET", "/users")

    assert found.handler is users
    assert found.path_params == {}


def test_router_matches_the_root_path() -> None:
    router, root = Router(), handler("root")
    router.add("GET", "/", root)

    assert matched(router, "GET", "/").handler is root


def test_router_unknown_path_is_not_found() -> None:
    router = Router()
    router.add("GET", "/users", handler("users"))

    assert match(router, "GET", "/missing") is None
    assert match(router, "GET", "/users/1") is None
    assert match(router, "GET", "/") is None


def test_router_path_without_leading_slash_is_not_found() -> None:
    router = Router()
    router.add("GET", "/users", handler("users"))

    assert match(router, "GET", "users") is None
    assert match(router, "GET", "") is None


def test_router_trailing_slash_is_a_different_path() -> None:
    router, plain, slash = Router(), handler("plain"), handler("slash")
    router.add("GET", "/users", plain)

    assert match(router, "GET", "/users/") is None

    router.add("GET", "/reports/", slash)
    assert matched(router, "GET", "/reports/").handler is slash
    assert match(router, "GET", "/reports") is None


def test_router_static_segments_are_compared_in_percent_encoded_form() -> None:
    router, cafe = Router(), handler("cafe")
    router.add("GET", "/café", cafe)

    assert matched(router, "GET", "/caf%C3%A9").handler is cafe


# --- parameters ---------------------------------------------------------


def test_router_captures_a_path_parameter() -> None:
    router, user = Router(), handler("user")
    router.add("GET", "/users/{user_id}", user)

    found = matched(router, "GET", "/users/42")

    assert found.handler is user
    assert found.path_params == {"user_id": "42"}


def test_router_captures_several_parameters_in_order() -> None:
    router = Router()
    router.add("GET", "/orgs/{org}/repos/{repo}", handler("repo"))

    found = matched(router, "GET", "/orgs/acme/repos/lettia")

    assert found.path_params == {"org": "acme", "repo": "lettia"}


def test_router_parameter_does_not_match_an_empty_segment() -> None:
    router = Router()
    router.add("GET", "/users/{id}", handler("user"))

    assert match(router, "GET", "/users/") is None
    assert match(router, "GET", "/users//") is None


def test_router_parameter_matches_exactly_one_segment() -> None:
    router = Router()
    router.add("GET", "/users/{id}", handler("user"))

    assert match(router, "GET", "/users/1/posts") is None


def test_router_parameter_values_are_percent_decoded() -> None:
    router = Router()
    router.add("GET", "/users/{id}", handler("user"))

    assert matched(router, "GET", "/users/caf%C3%A9").path_params == {"id": "café"}
    assert matched(router, "GET", "/users/a%20b").path_params == {"id": "a b"}


def test_router_invalid_utf8_in_a_parameter_is_replaced_not_raised() -> None:
    router = Router()
    router.add("GET", "/users/{id}", handler("user"))

    assert matched(router, "GET", "/users/%FF").path_params == {"id": "�"}


def test_router_encoded_slash_does_not_split_a_segment() -> None:
    router = Router()
    router.add("GET", "/files/{name}", handler("file"))
    router.add("GET", "/pairs/{a}/{b}", handler("pair"))

    assert matched(router, "GET", "/files/a%2Fb").path_params == {"name": "a/b"}
    assert match(router, "GET", "/pairs/a%2Fb") is None


def test_router_parameter_names_belong_to_each_route_not_the_tree_node() -> None:
    router, one, two = Router(), handler("one"), handler("two")
    router.add("GET", "/users/{id}", one)
    router.add("GET", "/users/{uid}/posts", two)

    assert matched(router, "GET", "/users/7").path_params == {"id": "7"}
    assert matched(router, "GET", "/users/7/posts").path_params == {"uid": "7"}


def test_router_same_shape_may_name_parameters_differently_per_method() -> None:
    router = Router()
    router.add("GET", "/users/{id}", handler("get"))
    router.add("PUT", "/users/{uid}", handler("put"))

    assert matched(router, "GET", "/users/1").path_params == {"id": "1"}
    assert matched(router, "PUT", "/users/1").path_params == {"uid": "1"}


# --- wildcard -----------------------------------------------------------


def test_router_wildcard_captures_the_rest_of_the_path() -> None:
    router, files = Router(), handler("files")
    router.add("GET", "/static/*", files)

    found = matched(router, "GET", "/static/css/app.css")

    assert found.handler is files
    assert found.path_params == {"*": "css/app.css"}


def test_router_wildcard_may_be_empty_but_needs_the_slash() -> None:
    router = Router()
    router.add("GET", "/static/*", handler("files"))

    assert matched(router, "GET", "/static/").path_params == {"*": ""}
    assert match(router, "GET", "/static") is None


def test_router_wildcard_value_is_percent_decoded() -> None:
    router = Router()
    router.add("GET", "/static/*", handler("files"))

    assert matched(router, "GET", "/static/a%20b/c").path_params == {"*": "a b/c"}


def test_router_root_wildcard_matches_everything_under_slash() -> None:
    router = Router()
    router.add("GET", "/*", handler("any"))

    assert matched(router, "GET", "/a/b").path_params == {"*": "a/b"}
    assert matched(router, "GET", "/").path_params == {"*": ""}


def test_router_wildcard_keeps_parameters_captured_before_it() -> None:
    router = Router()
    router.add("GET", "/repos/{repo}/raw/*", handler("raw"))

    found = matched(router, "GET", "/repos/lettia/raw/src/a.py")

    assert found.path_params == {"repo": "lettia", "*": "src/a.py"}


# --- precedence ---------------------------------------------------------


def test_router_static_beats_parameter_beats_wildcard() -> None:
    router = Router()
    static, param, wild = handler("static"), handler("param"), handler("wild")
    router.add("GET", "/a/*", wild)
    router.add("GET", "/a/{x}", param)
    router.add("GET", "/a/me", static)

    assert matched(router, "GET", "/a/me").handler is static
    assert matched(router, "GET", "/a/other").handler is param
    assert matched(router, "GET", "/a/other/deeper").handler is wild


def test_router_backtracks_from_a_static_branch_to_a_parameter_branch() -> None:
    router, profile, posts = Router(), handler("profile"), handler("posts")
    router.add("GET", "/users/me/profile", profile)
    router.add("GET", "/users/{id}/posts", posts)

    assert matched(router, "GET", "/users/me/profile").handler is profile
    found = matched(router, "GET", "/users/me/posts")
    assert found.handler is posts
    assert found.path_params == {"id": "me"}


def test_router_backtracks_from_a_parameter_branch_to_the_wildcard() -> None:
    router, special, rest = Router(), handler("special"), handler("rest")
    router.add("GET", "/a/{x}/b", special)
    router.add("GET", "/a/*", rest)

    assert matched(router, "GET", "/a/1/b").handler is special
    found = matched(router, "GET", "/a/1/c")
    assert found.handler is rest
    assert found.path_params == {"*": "1/c"}


# --- methods ------------------------------------------------------------


def test_router_selects_the_handler_by_method() -> None:
    router, get, post = Router(), handler("get"), handler("post")
    router.add("GET", "/users", get)
    router.add("POST", "/users", post)

    assert matched(router, "GET", "/users").handler is get
    assert matched(router, "POST", "/users").handler is post


def test_router_method_names_are_case_insensitive_on_registration() -> None:
    router, get = Router(), handler("get")
    router.add("get", "/users", get)

    assert matched(router, "GET", "/users").handler is get


def test_router_head_falls_back_to_the_get_handler() -> None:
    router, get = Router(), handler("get")
    router.add("GET", "/users", get)

    assert matched(router, "HEAD", "/users").handler is get


def test_router_explicit_head_handler_wins_over_the_get_fallback() -> None:
    router, get, head = Router(), handler("get"), handler("head")
    router.add("GET", "/users", get)
    router.add("HEAD", "/users", head)

    assert matched(router, "HEAD", "/users").handler is head


def test_router_wrong_method_is_405_listing_the_allowed_methods() -> None:
    router = Router()
    router.add("POST", "/users", handler("post"))
    router.add("GET", "/users", handler("get"))

    result = match(router, "DELETE", "/users")

    assert isinstance(result, MethodNotAllowed)
    assert result.allow == ("GET", "HEAD", "POST")


def test_router_405_does_not_advertise_head_without_a_get_route() -> None:
    router = Router()
    router.add("POST", "/users", handler("post"))

    result = match(router, "GET", "/users")

    assert isinstance(result, MethodNotAllowed)
    assert result.allow == ("POST",)


def test_router_method_mismatch_on_one_branch_falls_through_to_another() -> None:
    router, param_post = Router(), handler("param-post")
    router.add("GET", "/users/me", handler("me-get"))
    router.add("POST", "/users/{id}", param_post)

    assert matched(router, "POST", "/users/me").handler is param_post


def test_router_405_merges_the_methods_of_every_matching_branch() -> None:
    router = Router()
    router.add("GET", "/users/me", handler("me"))
    router.add("POST", "/users/{id}", handler("create"))

    result = match(router, "DELETE", "/users/me")

    assert isinstance(result, MethodNotAllowed)
    assert result.allow == ("GET", "HEAD", "POST")


def test_router_405_exposes_the_allow_header_value() -> None:
    router = Router()
    router.add("GET", "/users", handler("get"))

    result = match(router, "POST", "/users")

    assert isinstance(result, MethodNotAllowed)
    assert result.allow_header == "GET, HEAD"


# --- registration errors ------------------------------------------------


def test_router_duplicate_method_and_path_is_a_conflict() -> None:
    router = Router()
    router.add("GET", "/users", handler("a"))

    with pytest.raises(RouteConflict, match="GET /users"):
        router.add("GET", "/users", handler("b"))


def test_router_same_shape_with_other_parameter_names_is_a_conflict() -> None:
    router = Router()
    router.add("GET", "/users/{id}", handler("a"))

    with pytest.raises(RouteConflict, match="/users/{uid}"):
        router.add("GET", "/users/{uid}", handler("b"))


def test_router_duplicate_wildcard_is_a_conflict() -> None:
    router = Router()
    router.add("GET", "/files/*", handler("a"))

    with pytest.raises(RouteConflict):
        router.add("GET", "/files/*", handler("b"))


def test_router_conflict_is_a_value_error() -> None:
    assert issubclass(RouteConflict, ValueError)


def test_router_same_path_with_different_methods_is_not_a_conflict() -> None:
    router = Router()
    router.add("GET", "/users", handler("a"))
    router.add("POST", "/users", handler("b"))


def test_router_parameter_and_wildcard_may_share_a_prefix() -> None:
    router = Router()
    router.add("GET", "/files/{name}", handler("a"))
    router.add("GET", "/files/*", handler("b"))


@pytest.mark.parametrize(
    "path",
    [
        "users",
        "",
        "/a/{}",
        "/a/{1x}",
        "/a/{a-b}",
        "/a/x{id}",
        "/a/{id}x",
        "/a/{id",
        "/a/id}",
        "/a/{id}/{id}",
        "/a/*/b",
        "/a/b*",
        "/a/**",
    ],
)
def test_router_invalid_path_templates_are_rejected(path: str) -> None:
    with pytest.raises(ValueError, match="path"):
        Router().add("GET", path, handler("x"))


@pytest.mark.parametrize("method", ["", "GE T", "GET\r\n"])
def test_router_invalid_method_is_rejected(method: str) -> None:
    with pytest.raises(ValueError, match="method"):
        Router().add(method, "/x", handler("x"))
