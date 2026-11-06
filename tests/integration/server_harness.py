"""Start an ASGI app under uvicorn, hypercorn or granian in a subprocess."""

import importlib.util
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).parents[2]
SERVERS = ("uvicorn", "hypercorn", "granian")


def commands(port: int, app: str) -> dict[str, tuple[str, list[str]]]:
    """server name -> (module that must be installed, command line)."""
    py = sys.executable
    return {
        "uvicorn": (
            "uvicorn",
            [py, "-m", "uvicorn", app, "--port", str(port), "--log-level", "warning"],
        ),
        "hypercorn": (
            "hypercorn",
            [
                py,
                "-m",
                "hypercorn",
                app,
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
                app,
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


@contextmanager
def serve(name: str, app: str, ready_path: str) -> Iterator[str]:
    """Run ``app`` under server ``name``; yield its base URL once it answers."""
    port = free_port()
    module, command = commands(port, app)[name]
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
                    httpx.get(f"{url}{ready_path}", timeout=1)
                    break
                except httpx.TransportError:
                    if time.monotonic() > deadline:
                        pytest.fail(f"{name} did not start in time")
                    time.sleep(0.25)
            yield url
        finally:
            stop(process)
