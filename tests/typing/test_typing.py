"""Typing tests: positive fixtures are clean, negative ones match snapshots.

Each checker runs once per session over every fixture; diagnostics are
normalized to ``(line, code)`` pairs per file and compared with the committed
snapshots in ``snapshots/``. Set ``LETTIA_UPDATE_TYPING_SNAPSHOTS=1`` to rewrite
them; a checker upgrade that changes a snapshot must be explained in the PR.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TYPING_DIR = Path(__file__).parent
ROOT = TYPING_DIR.parents[1]
CHECKERS = ("pyrefly", "pyright", "mypy")

type Diagnostics = dict[str, list[tuple[int, str]]]


def fixtures(kind: str) -> list[Path]:
    return sorted((TYPING_DIR / kind).glob("*.py"))


def _executable(name: str) -> str:
    scripts = Path(sys.executable).parent
    found = shutil.which(name, path=str(scripts))
    if found is None:
        raise FileNotFoundError(f"{name} is not installed next to {sys.executable}")
    return found


def _relative(path: str) -> str:
    p = Path(path)
    p = p if p.is_absolute() else ROOT / p
    return p.resolve().relative_to(ROOT).as_posix()


def _run(args: list[str]) -> str:
    # Fixed argv built from the project's own tool names; no shell involved.
    done = subprocess.run(  # noqa: S603
        args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False
    )
    return done.stdout


def _pyrefly(files: list[str]) -> Diagnostics:
    out = _run([_executable("pyrefly"), "check", "--output-format", "json", *files])
    found: Diagnostics = {}
    for e in json.loads(out)["errors"]:
        found.setdefault(_relative(e["path"]), []).append((e["line"], e["name"]))
    return found


def _pyright(files: list[str]) -> Diagnostics:
    out = _run([_executable("pyright"), "--outputjson", *files])
    found: Diagnostics = {}
    for d in json.loads(out)["generalDiagnostics"]:
        if d["severity"] != "error":
            continue
        line = d["range"]["start"]["line"] + 1
        found.setdefault(_relative(d["file"]), []).append(
            (line, d.get("rule", "syntax"))
        )
    return found


_MYPY_LINE = re.compile(r"^(?P<path>.+?):(?P<line>\d+): error: .*\[(?P<code>[\w-]+)\]$")


def _mypy(files: list[str]) -> Diagnostics:
    out = _run(
        [
            _executable("mypy"),
            "--strict",
            "--no-error-summary",
            "--show-error-codes",
            "--no-pretty",
            "--hide-error-context",
            *files,
        ]
    )
    found: Diagnostics = {}
    for raw in out.splitlines():
        m = _MYPY_LINE.match(raw)
        if m:
            found.setdefault(_relative(m["path"]), []).append(
                (int(m["line"]), m["code"])
            )
    return found


_RUNNERS = {"pyrefly": _pyrefly, "pyright": _pyright, "mypy": _mypy}


@functools.cache
def check_all(checker: str) -> Diagnostics:
    """Diagnostics of ``checker`` for every positive and negative fixture."""
    files = [
        _relative(str(f)) for kind in ("positive", "negative") for f in fixtures(kind)
    ]
    found = _RUNNERS[checker](files)
    return {name: sorted(set(items)) for name, items in found.items()}


# Fixtures a checker is known not to flag. Each entry needs a reason; the
# guarantee is still enforced at startup (see "Startup checks" in the design).
KNOWN_GAPS: dict[tuple[str, str], str] = {
    (
        "pyrefly",
        "tests/typing/negative/neg_request_not_struct.py",
    ): "pyrefly 1.4.0 does not check TypeVar bounds in callable parameter position",
}

_SNAPSHOT_DIR = TYPING_DIR / "snapshots"
_UPDATE = os.environ.get("LETTIA_UPDATE_TYPING_SNAPSHOTS") == "1"


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _load_snapshot(checker: str) -> dict[str, list[list[int | str]]]:
    path = _SNAPSHOT_DIR / f"{checker}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _store_snapshot(checker: str) -> None:
    data = {_rel(f): check_all(checker).get(_rel(f), []) for f in fixtures("negative")}
    _SNAPSHOT_DIR.mkdir(exist_ok=True)
    (_SNAPSHOT_DIR / f"{checker}.json").write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


@pytest.mark.parametrize("checker", CHECKERS)
@pytest.mark.parametrize("fixture", fixtures("positive"), ids=lambda p: p.stem)
def test_positive_fixture_reports_no_errors(checker: str, fixture: Path) -> None:
    assert check_all(checker).get(_rel(fixture), []) == []


@pytest.mark.parametrize("checker", CHECKERS)
@pytest.mark.parametrize("fixture", fixtures("negative"), ids=lambda p: p.stem)
def test_negative_fixture_reports_an_error(checker: str, fixture: Path) -> None:
    errors = check_all(checker).get(_rel(fixture), [])
    if (checker, _rel(fixture)) in KNOWN_GAPS:
        assert errors == [], "known gap closed: remove it from KNOWN_GAPS"
    else:
        assert errors, f"{checker} reported nothing for {fixture.name}"


@pytest.mark.parametrize("checker", CHECKERS)
def test_negative_snapshot_matches_checker_output(checker: str) -> None:
    if _UPDATE:
        _store_snapshot(checker)
    expected = _load_snapshot(checker)
    actual = {
        _rel(f): [list(d) for d in check_all(checker).get(_rel(f), [])]
        for f in fixtures("negative")
    }
    assert actual == expected
