import pytest

from lettia.router import Router


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


def test_wildcard_routing() -> None:
    router = Router()
    router.add_route("GET", "/static/*filepath", "static_handler")

    res = router.match("GET", "/static/css/main.css")
    assert res is not None
    route, params = res
    assert route.handler == "static_handler"
    assert params == {"filepath": "css/main.css"}


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


def test_root_wildcard_matches_empty_path() -> None:
    router = Router()
    router.add_route("GET", "/*filepath", "files")

    result = router.match("GET", "/")

    assert result is not None
    assert result[0].handler == "files"
    assert result[1] == {"filepath": ""}


def test_duplicate_routes_are_rejected() -> None:
    router = Router()
    router.add_route("GET", "/items", "first")

    with pytest.raises(ValueError, match="already registered"):
        router.add_route("GET", "/items", "second")


def test_duplicate_route_names_are_rejected() -> None:
    router = Router()
    router.add_route("GET", "/first", "first", name="item")

    with pytest.raises(ValueError, match="already registered"):
        router.add_route("GET", "/second", "second", name="item")
