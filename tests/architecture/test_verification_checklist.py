"""spec/verification.md stays in step with the source tree and the test suite.

The checklist maps every module to its responsibility and to the evidence for
each behavior. These tests check its structure and that every reference
resolves; whether a test really asserts its claim is checked in review.
"""

import ast
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).parents[2]
CHECKLIST = ROOT / "spec" / "verification.md"
GLOBAL = "GLOBAL"

_MODULE = re.compile(r"^### `(src/lettia/[\w/]+\.py)`$")
_ITEM = re.compile(
    r"^- \[ \] (?P<id>[A-Z0-9-]+-\d{2}): (?P<claim>\S.*?) Evidence: (?P<evidence>.+)$"
)
_GAP = re.compile(r"^GAP \([^()]+\)$")
_REFS = re.compile(r"^`[^`]+`(?:, `[^`]+`)*$")
_NODE = re.compile(r"^(?P<file>tests/[\w/]+\.py)::(?P<name>test_\w+)(?:\[[^\]]+\])?$")
_FIXTURE = re.compile(r"^tests/typing/(?:positive|negative)/\w+\.py$")
_DOC = re.compile(r'^`(?P<file>(?:docs/[\w-]+|README)\.md)` "(?P<heading>[^"]+)"$')
_HEADING = re.compile(r"^#{1,6} (?P<text>.+)$")


@dataclass
class Item:
    id: str
    evidence: str
    line: int


@dataclass
class Section:
    name: str  # a module path such as "src/lettia/core/app.py", or GLOBAL
    responsibility: list[str] = field(default_factory=list[str])
    docs: list[tuple[int, str]] = field(default_factory=list[tuple[int, str]])
    items: list[Item] = field(default_factory=list[Item])
    malformed: list[int] = field(default_factory=list[int])


def _unfenced(path: Path) -> list[tuple[int, str]]:
    """Numbered lines of ``path`` outside fenced code blocks."""
    lines: list[tuple[int, str]] = []
    fenced = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced:
            lines.append((number, line))
    return lines


@cache
def _sections() -> list[Section]:
    found: list[Section] = []
    current: Section | None = None
    for number, line in _unfenced(CHECKLIST):
        if line.startswith(("## ", "### ")):
            module = _MODULE.match(line)
            current = (
                Section(GLOBAL)
                if line == "## Global invariants"
                else Section(module.group(1))
                if module
                else None
            )
            if current is not None:
                found.append(current)
        elif current is None:
            continue
        elif line.startswith("Responsibility: "):
            current.responsibility.append(line.removeprefix("Responsibility: ").strip())
        elif line.startswith("Docs: "):
            current.docs.append((number, line.removeprefix("Docs: ").strip()))
        elif line.startswith("- "):
            item = _ITEM.match(line)
            if item is None:
                current.malformed.append(number)
            else:
                current.items.append(
                    Item(item.group("id"), item.group("evidence"), number)
                )
    return found


def _id_prefix(name: str) -> str:
    if name == GLOBAL:
        return GLOBAL
    parts = PurePosixPath(name).relative_to("src/lettia").with_suffix("").parts
    return "-".join(part.strip("_").replace("_", "-").upper() for part in parts)


@cache
def _defined_functions(file: str) -> frozenset[str]:
    tree = ast.parse((ROOT / file).read_text(encoding="utf-8"))
    return frozenset(
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    )


@cache
def _headings(file: str) -> frozenset[str]:
    found: set[str] = set()
    for _, line in _unfenced(ROOT / file):
        if match := _HEADING.match(line):
            found.add(match.group("text").strip())
    return frozenset(found)


def _evidence_problems(item: Item) -> list[str]:
    if _GAP.fullmatch(item.evidence):
        return []
    if not _REFS.fullmatch(item.evidence):
        return [
            f"spec/verification.md:{item.line}: malformed evidence: {item.evidence}"
        ]
    problems: list[str] = []
    for ref in re.findall(r"`([^`]+)`", item.evidence):
        if node := _NODE.fullmatch(ref):
            file = node.group("file")
            name = node.group("name")
            if not (ROOT / file).is_file():
                problems.append(
                    f"spec/verification.md:{item.line}: missing test file: {file}"
                )
            elif name not in _defined_functions(file):
                problems.append(
                    f"spec/verification.md:{item.line}: missing test function: "
                    f"{file}::{name}"
                )
        elif _FIXTURE.fullmatch(ref):
            if not (ROOT / ref).is_file():
                problems.append(
                    f"spec/verification.md:{item.line}: missing typing fixture: {ref}"
                )
        else:
            problems.append(
                f"spec/verification.md:{item.line}: "
                f"not a test node or typing fixture: {ref}"
            )
    return problems


def _referenced_files() -> set[str]:
    files: set[str] = set()
    for section in _sections():
        for item in section.items:
            if not _REFS.fullmatch(item.evidence):
                continue
            for ref in re.findall(r"`([^`]+)`", item.evidence):
                if node := _NODE.fullmatch(ref):
                    files.add(node.group("file"))
                elif _FIXTURE.fullmatch(ref):
                    files.add(ref)
    return files


def test_checklist_lists_every_source_module_exactly_once() -> None:
    sections = _sections()
    actual = sorted(s.name for s in sections if s.name != GLOBAL)
    expected = sorted(
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "src" / "lettia").rglob("*.py")
        if "__pycache__" not in p.parts
    )
    assert actual == expected
    assert [s.name for s in sections].count(GLOBAL) == 1


def test_checklist_sections_state_responsibility_docs_and_items() -> None:
    offenders: list[str] = []
    for s in _sections():
        if s.name == GLOBAL:
            if not s.items:
                offenders.append(f"{s.name}: no items")
            continue
        if len(s.responsibility) != 1 or not s.responsibility[0]:
            offenders.append(f"{s.name}: expected 1 non-empty Responsibility line")
        if len(s.docs) != 1:
            offenders.append(f"{s.name}: expected 1 Docs line")
        if not s.items:
            offenders.append(f"{s.name}: no items")
    assert offenders == []


def test_checklist_bullets_in_checked_sections_are_items() -> None:
    offenders = [
        f"spec/verification.md:{line}: malformed bullet in {s.name}"
        for s in _sections()
        for line in s.malformed
    ]
    assert offenders == []


def test_checklist_item_ids_are_unique_and_carry_their_section_prefix() -> None:
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    bad_prefix: list[str] = []
    for s in _sections():
        prefix = _id_prefix(s.name)
        pattern = re.compile(rf"{re.escape(prefix)}-\d{{2}}")
        for item in s.items:
            if item.id in seen:
                duplicates.append(
                    f"spec/verification.md:{item.line}: duplicate id {item.id}"
                    f" (first at line {seen[item.id]})"
                )
            else:
                seen[item.id] = item.line
            if not pattern.fullmatch(item.id):
                bad_prefix.append(
                    f"spec/verification.md:{item.line}: id {item.id}"
                    f" does not match {prefix}-NN"
                )
    assert duplicates == []
    assert bad_prefix == []


def test_checklist_evidence_resolves_to_existing_tests_and_fixtures() -> None:
    offenders = [
        problem
        for s in _sections()
        for item in s.items
        for problem in _evidence_problems(item)
    ]
    assert offenders == []


def test_checklist_docs_lines_name_existing_headings() -> None:
    offenders: list[str] = []
    for s in _sections():
        if s.name == GLOBAL:
            continue
        for line_no, raw in s.docs:
            if raw == "none":
                continue
            for part in raw.split("; "):
                match = _DOC.fullmatch(part)
                if match is None:
                    offenders.append(
                        f"spec/verification.md:{line_no}: malformed doc ref: {part}"
                    )
                    continue
                doc_file = match.group("file")
                heading = match.group("heading")
                if not (ROOT / doc_file).is_file():
                    offenders.append(
                        f"spec/verification.md:{line_no}: missing doc file: {doc_file}"
                    )
                elif heading not in _headings(doc_file):
                    offenders.append(
                        f"spec/verification.md:{line_no}: missing heading"
                        f' "{heading}" in {doc_file}'
                    )
    assert offenders == []


def test_checklist_references_every_test_file_and_typing_fixture() -> None:
    expected = {
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "tests").rglob("test_*.py")
        if "__pycache__" not in p.parts
    }
    expected.update(
        p.relative_to(ROOT).as_posix()
        for kind in ("positive", "negative")
        for p in (ROOT / "tests" / "typing" / kind).glob("*.py")
    )
    assert expected - _referenced_files() == set()
