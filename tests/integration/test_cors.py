import pytest

from lettia import App
from lettia.middleware import (
    cors,
)
from lettia.testing import TestClient


@pytest.mark.contract("MW-CORS")
@pytest.mark.parametrize("credentials", [False, True])
@pytest.mark.parametrize("wildcard", [False, True])
def test_cors_preflight_emits_explicit_permissions(
    credentials: bool, wildcard: bool
) -> None:
    app = App()
    app.use(
        cors(
            allow_origins=["https://frontend.example"],
            allow_credentials=credentials,
            allow_headers=["*"] if wildcard else ["content-type"],
            allow_methods=["*"] if wildcard else ["GET"],
        )
    )

    response = TestClient(app).request(
        "OPTIONS",
        "/missing",
        headers={
            "Origin": "https://frontend.example",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": (
                "Content-Type, Authorization, CONTENT-TYPE"
            ),
        },
    )

    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "https://frontend.example"
    assert response.headers.get("access-control-allow-credentials") == (
        "true" if credentials else None
    )
    assert response.headers["access-control-allow-headers"] == (
        "content-type, authorization" if wildcard else "content-type"
    )
    assert response.headers["access-control-allow-methods"] == (
        "PATCH" if wildcard else "GET"
    )
    expected = {"origin"}
    if wildcard:
        expected |= {"access-control-request-headers", "access-control-request-method"}
    assert {
        token.strip().lower() for token in response.headers["vary"].split(",")
    } == expected


@pytest.mark.contract("MW-CORS")
def test_cors_ordinary_options_reaches_registered_route() -> None:
    app = App()
    app.use(cors())
    app.add_route("OPTIONS", "/", lambda ctx: "route options")

    response = TestClient(app).request(
        "OPTIONS", "/", headers={"Origin": "https://example.com"}
    )

    assert response.status_code == 200
    assert response.text == "route options"


@pytest.mark.contract("MW-CORS")
def test_global_cors_handles_preflight_without_options_route() -> None:
    app = App()
    app.use(cors(allow_origins=["https://example.com"]))
    app.add_route("GET", "/resource", lambda ctx: "ok")
    response = TestClient(app).request(
        "OPTIONS",
        "/resource",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "https://example.com"
