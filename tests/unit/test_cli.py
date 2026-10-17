import json
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from lettia.cli import main

APP_SOURCE = textwrap.dedent(
    """
    from typing import Protocol

    from lettia import App, Context, NoContent, Ok, op, public


    class PingApi(Protocol):
        @op.get("/ping", guard=public)
        async def ping(self, c: Context[None], req: None) -> Ok[str]:
            \"\"\"Answer a ping.\"\"\"
            ...


    class PingService(PingApi):
        async def ping(self, c: Context[None], req: None) -> Ok[str]:
            return Ok("pong")


    app = App(title="Ping", version="1.0")
    app.group("/v1").mount(PingService())
    """
)

BROKEN_SOURCE = textwrap.dedent(
    """
    from lettia import App

    app = App()
    app.get("no-slash", None)
    """
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A directory with importable app modules, made the working directory."""
    (tmp_path / "ping_app.py").write_text(APP_SOURCE, encoding="utf-8")
    (tmp_path / "broken_app.py").write_text(BROKEN_SOURCE, encoding="utf-8")
    (tmp_path / "not_an_app.py").write_text("app = 'text'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    for name in ("ping_app", "broken_app", "not_an_app"):
        sys.modules.pop(name, None)
    if str(tmp_path) in sys.path:
        sys.path.remove(str(tmp_path))


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- a successful export ------------------------------------------------


def test_export_prints_the_document_as_indented_json(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(capsys, "openapi", "export", "ping_app:app")

    assert (code, err) == (0, "")
    document = json.loads(out)
    assert document["info"] == {"title": "Ping", "version": "1.0"}
    assert list(document["paths"]) == ["/v1/ping"]
    assert out.endswith("}\n")
    assert out.startswith('{\n  "openapi": "3.1.0"')


def test_export_twice_gives_identical_text(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = run(capsys, "openapi", "export", "ping_app:app")
    sys.modules.pop("ping_app")
    second = run(capsys, "openapi", "export", "ping_app:app")

    assert first == second


def test_export_writes_a_file_with_unix_line_endings(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = project / "out" / "openapi.json"
    target.parent.mkdir()

    code, out, _ = run(
        capsys, "openapi", "export", "ping_app:app", "--output", str(target)
    )

    assert (code, out) == (0, "")
    data = target.read_bytes()
    assert b"\r" not in data
    assert json.loads(data)["info"]["title"] == "Ping"


def test_export_as_yaml(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    yaml = pytest.importorskip("yaml")

    code, out, _ = run(capsys, "openapi", "export", "ping_app:app", "--format", "yaml")

    assert code == 0
    assert out.startswith("openapi: 3.1.0\n")
    assert yaml.safe_load(out)["info"]["title"] == "Ping"


def test_export_as_yaml_without_pyyaml_explains_how_to_install_it(
    project: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "yaml", None)

    code, out, err = run(
        capsys, "openapi", "export", "ping_app:app", "--format", "yaml"
    )

    assert (code, out) == (2, "")
    assert "lettia[yaml]" in err


# --- errors ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("ping_app", "module:attribute"),
        ("missing_module:app", "missing_module"),
        ("ping_app:nothing", "nothing"),
        ("not_an_app:app", "lettia.App"),
    ],
)
def test_export_reports_a_bad_application_reference(
    project: Path,
    capsys: pytest.CaptureFixture[str],
    target: str,
    expected: str,
) -> None:
    code, out, err = run(capsys, "openapi", "export", target)

    assert (code, out) == (2, "")
    assert err.startswith("lettia: error:")
    assert expected in err


def test_export_reports_every_startup_problem_and_fails(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(capsys, "openapi", "export", "broken_app:app")

    assert (code, out) == (1, "")
    assert "no-slash" in err


def test_unknown_format_is_a_usage_error(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["openapi", "export", "ping_app:app", "--format", "toml"])

    assert caught.value.code == 2


# --- entry points ---------------------------------------------------------


def test_python_dash_m_lettia_runs_the_cli(project: Path) -> None:
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "lettia", "openapi", "export", "ping_app:app"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["info"]["title"] == "Ping"
