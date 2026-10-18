"""lettia: a contract-first ASGI framework.

The public names are imported on first use, so ``import lettia.core`` (or
``lettia.middleware``) does not drag in the contract layer above it.
"""

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lettia.app import App, Group
    from lettia.contract import (
        ApiKeyHeader,
        Body,
        Context,
        Cookie,
        Created,
        File,
        Form,
        GuardSpec,
        Header,
        HeaderName,
        HttpBearer,
        NoContent,
        NotFound,
        Ok,
        Path,
        Problem,
        Query,
        Reply,
        UploadFile,
        op,
        public,
    )
    from lettia.core import Key, RequestHead

# public name -> the module that defines it
_EXPORTS: dict[str, str] = {
    "App": "lettia.app",
    "Group": "lettia.app",
    "Key": "lettia.core",
    "RequestHead": "lettia.core",
    **dict.fromkeys(
        (
            "ApiKeyHeader",
            "Body",
            "Context",
            "Cookie",
            "Created",
            "File",
            "Form",
            "GuardSpec",
            "Header",
            "HeaderName",
            "HttpBearer",
            "NoContent",
            "NotFound",
            "Ok",
            "Path",
            "Problem",
            "Query",
            "Reply",
            "UploadFile",
            "op",
            "public",
        ),
        "lettia.contract",
    ),
}

__all__ = [
    "ApiKeyHeader",
    "App",
    "Body",
    "Context",
    "Cookie",
    "Created",
    "File",
    "Form",
    "Group",
    "GuardSpec",
    "Header",
    "HeaderName",
    "HttpBearer",
    "Key",
    "NoContent",
    "NotFound",
    "Ok",
    "Path",
    "Problem",
    "Query",
    "Reply",
    "RequestHead",
    "UploadFile",
    "op",
    "public",
]


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value  # later lookups skip this function
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
