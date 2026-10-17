"""The lower layers can be used without importing the layers above them.

The static rules in ``test_layers.py`` keep imports pointing downward. These
tests check what actually gets loaded, in a fresh interpreter, because the
package ``__init__`` runs whenever any submodule is imported.
"""

import subprocess
import sys

import pytest

import lettia


def loaded_after(statement: str) -> set[str]:
    """The ``lettia`` modules a fresh interpreter has loaded after ``statement``."""
    code = (
        f"{statement}\n"
        "import sys\n"
        "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('lettia'))))"
    )
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    return set(done.stdout.split())


@pytest.mark.parametrize(
    "statement", ["import lettia.core", "from lettia.core import App, Router"]
)
def test_core_loads_without_the_contract_layer(statement: str) -> None:
    modules = loaded_after(statement)

    assert "lettia.core" in modules
    assert not {m for m in modules if m.startswith(("lettia.contract", "lettia.app"))}


def test_middleware_loads_without_the_contract_layer() -> None:
    modules = loaded_after("import lettia.middleware")

    assert "lettia.middleware" in modules
    assert not {m for m in modules if m.startswith(("lettia.contract", "lettia.app"))}


def test_importing_the_top_level_package_loads_nothing_else() -> None:
    assert loaded_after("import lettia") == {"lettia"}


def test_a_public_name_loads_the_layer_it_comes_from_on_first_use() -> None:
    modules = loaded_after("from lettia import App")

    assert {"lettia.app", "lettia.contract"} <= modules


def test_every_public_name_resolves() -> None:
    for name in lettia.__all__:
        assert getattr(lettia, name) is not None, name


def test_public_names_are_listed_by_dir() -> None:
    assert set(lettia.__all__) <= set(dir(lettia))


def test_an_unknown_name_is_an_attribute_error() -> None:
    with pytest.raises(AttributeError, match="no_such_name"):
        lettia.no_such_name  # type: ignore[attr-defined]  # noqa: B018
