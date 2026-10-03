"""Classify collected tests and enforce the contract registry before selection."""

import tomllib
from collections import Counter, defaultdict
from pathlib import Path
from typing import cast

import pytest

pytest_plugins = ("pytester",)

LAYERS = {"unit", "integration", "smoke", "architecture"}
TEST_ROOT = Path(__file__).resolve().parent
REGISTRY_KEY = pytest.StashKey[dict[str, set[str]]]()
REPORT_KEY = pytest.StashKey[dict[str, tuple[int, int]]]()
CONTRACT_REPORT_KEY = pytest.StashKey[dict[str, set[str]]]()


def pytest_configure(config: pytest.Config) -> None:
    for layer in sorted(LAYERS):
        config.addinivalue_line("markers", f"{layer}: derived from the test directory")
    config.addinivalue_line(
        "markers", "contract(*ids): contracts verified by this test"
    )
    with (TEST_ROOT / "contracts.toml").open("rb") as source:
        registry: dict[str, object] = tomllib.load(source)
    entries = cast(dict[str, dict[str, object]], registry["contracts"])
    required: dict[str, set[str]] = {}
    for identity, entry in entries.items():
        for field in ("owner", "rule", "inputs", "outputs", "errors", "side_effects"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise pytest.UsageError(f"{identity}: missing contract field {field}")
        levels = entry.get("required_layers")
        if not isinstance(levels, list):
            raise pytest.UsageError(f"{identity}: required_layers must be a list")
        level_values = cast(list[object], levels)
        if not level_values or not all(
            isinstance(x, str) and x in LAYERS for x in level_values
        ):
            raise pytest.UsageError(f"{identity}: invalid required_layers")
        required[identity] = set(cast(list[str], level_values))
    config.stash[REGISTRY_KEY] = required


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    required = config.stash[REGISTRY_KEY]
    covered: dict[str, set[str]] = defaultdict(set)
    for item in items:
        relative = item.path.relative_to(TEST_ROOT)
        layer = relative.parts[0]
        if layer not in LAYERS:
            raise pytest.UsageError(f"Unclassified test: {item.nodeid}")
        if next((m for m in item.iter_markers() if m.name in LAYERS), None):
            raise pytest.UsageError(
                f"Layer markers must come from directories: {item.nodeid}"
            )
        item.add_marker(getattr(pytest.mark, layer))
        identifiers = [
            identity for mark in item.iter_markers("contract") for identity in mark.args
        ]
        if not identifiers:
            raise pytest.UsageError(f"Missing contract: {item.nodeid}")
        for identity in identifiers:
            if not isinstance(identity, str) or identity not in required:
                raise pytest.UsageError(f"Unknown contract {identity!r}: {item.nodeid}")
            covered[identity].add(layer)
    # A focused path cannot satisfy every layer. Full-tree collection checks
    # inventory before -m/-k selection, so fast marker runs remain meaningful.
    full_tree = any(
        Path(argument.split("::")[0]).resolve() in (TEST_ROOT, TEST_ROOT.parent)
        for argument in config.args
    )
    if full_tree:
        missing = {
            key: sorted(levels - covered[key])
            for key, levels in required.items()
            if levels - covered[key]
        }
        if missing:
            raise pytest.UsageError(f"Missing contract layers: {missing}")


def pytest_collection_finish(session: pytest.Session) -> None:
    cases: Counter[str] = Counter()
    functions: dict[str, set[str]] = defaultdict(set)
    contracts: dict[str, set[str]] = defaultdict(set)
    for item in session.items:
        layer = item.path.relative_to(TEST_ROOT).parts[0]
        cases[layer] += 1
        functions[layer].add(item.nodeid.split("[")[0])
        for marker in item.iter_markers("contract"):
            for identity in marker.args:
                contracts[str(identity)].add(layer)
    session.config.stash[REPORT_KEY] = {
        layer: (len(functions[layer]), cases[layer]) for layer in sorted(cases)
    }
    session.config.stash[CONTRACT_REPORT_KEY] = dict(contracts)


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    config = terminalreporter.config
    if REPORT_KEY not in config.stash:
        return
    terminalreporter.section("contract inventory (selected tests)")
    for layer, (functions, cases) in config.stash[REPORT_KEY].items():
        terminalreporter.write_line(f"{layer}: {functions} functions / {cases} cases")
    covered = config.stash[CONTRACT_REPORT_KEY]
    terminalreporter.write_line(
        f"Contracts: {len(covered)}/{len(config.stash[REGISTRY_KEY])}; "
        "generated Hypothesis examples are not counted as separate cases"
    )
    if config.option.collectonly:
        for identity, layers in sorted(covered.items()):
            terminalreporter.write_line(f"  {identity}: {', '.join(sorted(layers))}")
