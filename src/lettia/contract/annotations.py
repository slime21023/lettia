"""The one place where the framework reads annotations."""

import typing
from collections.abc import Callable
from typing import Any


def read_annotations(function: Callable[..., object]) -> dict[str, Any]:
    """Resolved annotations of ``function``, with ``Annotated`` metadata kept.

    Python 3.14 evaluates annotations lazily; ``typing.get_type_hints`` resolves
    them on every supported version, so a change in how annotations are read
    stays confined to this function.
    """
    return typing.get_type_hints(function, include_extras=True)
