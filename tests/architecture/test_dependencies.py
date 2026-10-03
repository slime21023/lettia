import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "src" / "lettia"


@pytest.mark.contract("ARCH-POLICY")
@pytest.mark.parametrize("module", ["_json", "_headers", "_conditional", "_binding"])
def test_pure_rules_have_no_framework_or_io_dependencies(module: str) -> None:
    tree = ast.parse((ROOT / f"{module}.py").read_text(encoding="utf-8"))
    allowed = {
        "lettia.asgi",
        "attrs",
        "typing",
        "collections.abc",
        "re",
        "dataclasses",
        "inspect",
        "types",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed, (module, node.module)
        elif isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)


@pytest.mark.contract("ARCH-POLICY", "JSON-VALUE")
@pytest.mark.parametrize("module", ["response", "websocket"])
def test_json_consumers_do_not_import_context(module: str) -> None:
    tree = ast.parse((ROOT / f"{module}.py").read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "lettia.context"
        for node in ast.walk(tree)
    )


@pytest.mark.contract("ARCH-POLICY", "APP-COMPLETE")
def test_app_uses_writer_queries_and_context_policy_methods() -> None:
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    forbidden = {
        "_start_attempted",
        "_start_ready",
        "_can_finish",
        "_body_complete",
        "_closing",
        "_cleanup_failed",
        "_write_lock",
        "_response_finalizers",
    }
    assert not {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden
    }
    assert not any(
        isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Store)
        and isinstance(node.value, ast.Name)
        and node.value.id == "writer"
        for node in ast.walk(tree)
    )
