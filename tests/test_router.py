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


def test_routing_precedence() -> None:
    router = Router()
    router.add_route("GET", "/files/spec", "static_spec")
    router.add_route("GET", "/files/:id", "param_file")
    router.add_route("GET", "/files/*all", "wildcard_file")

    # Static match exact
    res_static = router.match("GET", "/files/spec")
    assert res_static is not None
    assert res_static[0].handler == "static_spec"

    # Param match single segment
    res_param = router.match("GET", "/files/123")
    assert res_param is not None
    assert res_param[0].handler == "param_file"

    # Wildcard match multi segment
    res_wildcard = router.match("GET", "/files/123/sub/item")
    assert res_wildcard is not None
    assert res_wildcard[0].handler == "wildcard_file"
    assert res_wildcard[1] == {"all": "123/sub/item"}


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
