"""The ``lettia`` command line: currently ``lettia openapi export``."""

import argparse
import importlib
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import msgspec.yaml

from lettia.app import App
from lettia.core import StartupError


class _UsageError(Exception):
    """The command line asks for something that cannot be done."""


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line; the return value is the process exit code."""
    args = _parser().parse_args(argv)
    try:
        app = _load_app(args.app)
        document = app.openapi()
        text = _render(document, args.format)
        if args.output is None:
            sys.stdout.write(text)
        else:
            Path(args.output).write_text(text, encoding="utf-8", newline="\n")
    except StartupError as exc:
        print("lettia: the application cannot start:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    except _UsageError as exc:
        print(f"lettia: error: {exc}", file=sys.stderr)
        return 2
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lettia")
    commands = parser.add_subparsers(dest="command", required=True)
    openapi = commands.add_parser("openapi", help="work with the OpenAPI document")
    actions = openapi.add_subparsers(dest="action", required=True)
    export = actions.add_parser(
        "export", help="write the OpenAPI document of an application"
    )
    export.add_argument("app", help="the application, as module:attribute")
    export.add_argument("--format", choices=("json", "yaml"), default="json")
    export.add_argument("--output", help="write to this file instead of stdout")
    return parser


def _load_app(reference: str) -> App:
    module_name, separator, attribute = reference.partition(":")
    if not (separator and module_name and attribute):
        raise _UsageError(f"{reference!r} is not of the form module:attribute")
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise _UsageError(
            f"cannot import {module_name!r}: {type(exc).__name__}: {exc}"
        ) from None
    try:
        target: object = module
        for part in attribute.split("."):
            target = getattr(target, part)
    except AttributeError:
        raise _UsageError(
            f"module {module_name!r} has no attribute {attribute!r}"
        ) from None
    if not isinstance(target, App):
        raise _UsageError(f"{reference!r} is not a lettia.App")
    return target


def _render(document: dict[str, object], output_format: str) -> str:
    if output_format == "yaml":
        try:
            return msgspec.yaml.encode(document).decode()
        except ImportError:
            raise _UsageError(
                "YAML output needs PyYAML: pip install 'lettia[yaml]'"
            ) from None
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
