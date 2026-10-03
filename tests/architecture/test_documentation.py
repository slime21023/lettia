from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


FORBIDDEN_STATE_PATTERNS = (
    "app.state",
    "ctx.state[",
    "ws.state[",
    '.state.get("',
)


FORBIDDEN_TYPING_PATTERNS = ("A" + "ny", "type:" + " ignore")


@pytest.mark.contract("ARCH-POLICY")
def test_public_docs_and_examples_do_not_teach_legacy_state_apis() -> None:
    files = [ROOT / "README.md"]
    files.extend((ROOT / "docs").rglob("*.md"))
    files.extend((ROOT / "examples").rglob("*.py"))

    for path in files:
        content = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_STATE_PATTERNS:
            assert pattern not in content, f"{path} documents removed API: {pattern}"


@pytest.mark.contract("ARCH-POLICY")
def test_project_has_no_untyped_escape_hatches() -> None:
    files = []
    for directory, suffix in (("src", "*.py"), ("tests", "*.py"), ("examples", "*.py")):
        files.extend((ROOT / directory).rglob(suffix))
    files.extend((ROOT / "docs").rglob("*.md"))
    files.append(ROOT / "README.md")

    for path in files:
        content = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_TYPING_PATTERNS:
            assert pattern not in content, (
                f"{path} contains forbidden typing escape: {pattern}"
            )
