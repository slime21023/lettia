"""Lower layers must not know upper layers (design principle 2)."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parents[2] / "src" / "lettia"

# layer -> top-level lettia sub-packages it must never import
FORBIDDEN = {
    "core": {"contract", "middleware", "testing", "cli"},
    "middleware": {"contract", "testing", "cli"},
    "contract": {"middleware", "testing", "cli"},
}


def imported_lettia_modules(path: Path) -> set[str]:
    """Dotted names (``lettia`` and below) a file imports."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.split(".")[0] == "lettia")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] == "lettia":
                found.add(node.module)
                found.update(f"{node.module}.{a.name}" for a in node.names)
    return found


def layer_files(layer: str) -> list[Path]:
    return sorted((SRC / layer).rglob("*.py"))


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_does_not_import_forbidden_layers(layer: str) -> None:
    offenders = {
        f"{path.relative_to(SRC)} -> {name}"
        for path in layer_files(layer)
        for name in imported_lettia_modules(path)
        if len(parts := name.split(".")) > 1 and parts[1] in FORBIDDEN[layer]
    }

    assert offenders == set()


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_does_not_import_the_public_entry_point(layer: str) -> None:
    # ``from lettia import X`` would pull in every layer above this one.
    offenders = {
        str(path.relative_to(SRC))
        for path in layer_files(layer)
        if "lettia" in imported_lettia_modules(path)
    }

    assert offenders == set()


def test_layer_rules_cover_every_subpackage_that_exists() -> None:
    present = {p.name for p in SRC.iterdir() if p.is_dir() and p.name != "__pycache__"}

    assert present <= set(FORBIDDEN) | {"testing", "cli"}
