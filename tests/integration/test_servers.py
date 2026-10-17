"""The shipped example served by real ASGI servers.

The framework is not tied to one server, so the same checks run against each of
them. Besides the happy paths, two cases depend on server behaviour: the raw
request path (an encoded slash must stay inside one path segment) and chunked
request bodies.
"""

import importlib.util
import json
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.server

ROOT = Path(__file__).parents[2]
APP = "examples.users_api:app"
USER = {"authorization": "Bearer user-token"}
ADMIN = {"authorization": "Bearer admin-token"}


def commands(port: int) -> dict[str, tuple[str, list[str]]]:
    """server name -> (module that must be installed, command line)."""
    py = sys.executable
    return {
        "uvicorn": (
            "uvicorn",
            [py, "-m", "uvicorn", APP, "--port", str(port), "--log-level", "warning"],
        ),
        "hypercorn": (
            "hypercorn",
            [
                py,
                "-m",
                "hypercorn",
                APP,
                "--bind",
                f"127.0.0.1:{port}",
                "--log-level",
                "warning",
            ],
        ),
        "granian": (
            "granian",
            [
                py,
                "-m",
                "granian",
                "--interface",
                "asgi",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
                APP,
            ],
        ),
    }


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def stop(process: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":  # take the server's worker processes down with it
        subprocess.run(  # noqa: S603
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],  # noqa: S607
            capture_output=True,
            check=False,
        )
    else:
        process.terminate()
    process.wait(timeout=15)


@pytest.fixture(scope="module", params=["uvicorn", "hypercorn", "granian"])
def base_url(request: pytest.FixtureRequest) -> Iterator[str]:
    name: str = request.param
    port = free_port()
    module, command = commands(port)[name]
    if importlib.util.find_spec(module) is None:
        pytest.skip(f"{module} is not installed")

    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(  # noqa: S603
            command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT
        )
        url = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 40
            while True:
                if process.poll() is not None:
                    log.seek(0)
                    pytest.fail(f"{name} exited early:\n{log.read().decode()}")
                try:
                    httpx.get(f"{url}/healthz", timeout=1)
                    break
                except httpx.TransportError:
                    if time.monotonic() > deadline:
                        pytest.fail(f"{name} did not start in time")
                    time.sleep(0.25)
            yield url
        finally:
            stop(process)


@pytest.fixture
def http(base_url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=base_url, timeout=10) as client:
        yield client


# --- behaviour every server must show -------------------------------------


def test_the_app_starts_and_answers_the_health_probe(http: httpx.Client) -> None:
    res = http.get("/healthz")

    assert (res.status_code, res.json()) == (200, "ok")


def test_head_has_headers_and_no_body(http: httpx.Client) -> None:
    res = http.head("/healthz")

    assert res.status_code == 200
    assert res.content == b""
    assert int(res.headers["content-length"]) > 0


def test_guards_decide_who_gets_in(http: httpx.Client) -> None:
    assert http.get("/v1/users/1").status_code == 401
    assert http.get("/v1/users/1", headers=USER).json()["name"] == "Ann"


def test_query_parameters_reach_the_handler(http: httpx.Client) -> None:
    res = http.get("/v1/users", params={"limit": 1}, headers=USER)

    assert res.status_code == 200
    assert len(res.json()) == 1


def test_an_invalid_body_is_a_422_with_its_location(http: httpx.Client) -> None:
    res = http.post("/v1/users", json={"name": "Bo"}, headers=ADMIN)

    assert res.status_code == 422
    assert res.json()["errors"][0]["path"] == "$.body.email"


def test_unknown_paths_and_wrong_methods(http: httpx.Client) -> None:
    missing = http.get("/nothing")
    wrong = http.post("/healthz")

    assert missing.status_code == 404
    assert missing.headers["content-type"] == "application/problem+json"
    assert wrong.status_code == 405
    assert wrong.headers["allow"] == "GET, HEAD"


def test_an_encoded_slash_stays_inside_its_path_segment(http: httpx.Client) -> None:
    # Routed on the raw path, a%2Fb is one segment and reaches the handler's
    # decoding (a 422); a server that hands over a decoded path would 404.
    res = http.get("/v1/users/a%2Fb", headers=USER)

    assert res.status_code == 422
    assert res.json()["errors"][0]["path"] == "$.path.user_id"


def test_a_chunked_request_body_is_read_completely(http: httpx.Client) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"name": "Chunk", '
        yield b'"email": "c@example.com"}'

    created = http.post(
        "/v1/users",
        content=chunks(),
        headers={**ADMIN, "content-type": "application/json"},
    )

    assert created.status_code == 201
    assert created.json()["name"] == "Chunk"
    assert http.delete(created.headers["location"], headers=ADMIN).status_code == 204


def test_the_served_openapi_document_is_the_committed_one(http: httpx.Client) -> None:
    committed = json.loads((ROOT / "examples" / "openapi.json").read_text("utf-8"))

    assert http.get("/openapi.json").json() == committed
