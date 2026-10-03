from pathlib import Path

import pytest


@pytest.mark.contract("ARCH-POLICY")
@pytest.mark.parametrize(
    "location,marker,required,arguments,error",
    [
        ("unit", "contract('SAMPLE')", '["unit"]', ["."], None),
        ("unit", "contract('UNKNOWN')", '["unit"]', ["."], "Unknown contract"),
        ("unit", "", '["unit"]', ["."], "Missing contract"),
        ("other", "contract('SAMPLE')", '["unit"]', ["."], "Unclassified test"),
        (
            "unit",
            "contract('SAMPLE')",
            '["integration"]',
            ["."],
            "Missing contract layers",
        ),
        ("unit", "contract('SAMPLE')", '["integration"]', ["unit"], None),
        (
            "unit",
            "contract('SAMPLE')",
            '["integration"]',
            [".", "-k", "absent"],
            "Missing contract layers",
        ),
        (
            "unit",
            "unit\n@pytest.mark.contract('SAMPLE')",
            '["unit"]',
            ["."],
            "Layer markers must come from directories",
        ),
    ],
)
def test_collection_enforces_contracts_before_selection(
    pytester: pytest.Pytester,
    location: str,
    marker: str,
    required: str,
    arguments: list[str],
    error: str | None,
) -> None:
    source = Path(__file__).resolve().parents[1] / "conftest.py"
    pytester.makeconftest(source.read_text(encoding="utf-8"))
    pytester.makefile(
        ".toml",
        contracts=f"""
[contracts.SAMPLE]
owner = "sample"
rule = "sample"
inputs = "sample"
outputs = "sample"
errors = "sample"
side_effects = "sample"
required_layers = {required}
""",
    )
    destination = pytester.path / location
    destination.mkdir()
    decorator = f"@pytest.mark.{marker}\n" if marker else ""
    (destination / "test_sample.py").write_text(
        f"import pytest\n{decorator}def test_sample():\n    assert True\n",
        encoding="utf-8",
    )
    result = pytester.runpytest_subprocess(
        "--collect-only", "-q", *arguments, timeout=20
    )
    if error is None:
        assert result.ret == pytest.ExitCode.OK
        assert "1 test collected" in result.stdout.str()
    else:
        assert result.ret == pytest.ExitCode.USAGE_ERROR
        assert error in result.stderr.str()
