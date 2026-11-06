"""``mount_docs``: serve the app's OpenAPI document in an interactive viewer."""

import base64
import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from lettia.core.app import StartupCheck
from lettia.core.context import Context
from lettia.core.handler import Handler
from lettia.core.headers import MutableHeaders
from lettia.core.response import Response
from lettia.extensions.docs import pages
from lettia.extensions.docs.pages import (
    OFFLINE_CSP,
    PROVIDERS,
    THEMES,
    Provider,
    Theme,
    redoc_page,
    scalar_page,
    swagger_ui_page,
)
from lettia.extensions.staticfiles import StaticFiles


class DocsHost(Protocol):
    """What ``mount_docs`` needs from the app: ``lettia.App`` provides it."""

    @property
    def title(self) -> str: ...

    @property
    def openapi_url(self) -> str | None: ...

    def get(self, path: str, handler: Handler) -> None: ...

    def add_startup_check(self, check: StartupCheck) -> None: ...


_PAGES = {"scalar": scalar_page, "swagger": swagger_ui_page, "redoc": redoc_page}
_HTML_TYPE = (b"content-type", b"text/html; charset=utf-8")
_CSP = (b"content-security-policy", OFFLINE_CSP.encode("ascii"))


def _has_control(value: str) -> bool:
    return any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)


def _check_prefix(prefix: str) -> None:
    if not prefix.startswith("/"):
        raise ValueError(
            f"docs prefix must be a path starting with '/', got {prefix!r}"
        )
    if _has_control(prefix) or any(char in prefix for char in "?#*{}"):
        raise ValueError(
            f"docs prefix must be a plain path without query, fragment, control "
            f"characters, '*' or braces, got {prefix!r}"
        )


def _check_favicon(favicon: str, *, offline: bool) -> None:
    problem = (
        f"favicon must be a same-origin path ('/...') or an http(s) URL, "
        f"got {favicon!r}"
    )
    if _has_control(favicon) or "\\" in favicon:
        raise ValueError(problem)
    if favicon.startswith("/"):
        if favicon.startswith("//"):
            raise ValueError(problem)
        return
    parts = urlsplit(favicon)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(problem)
    if offline:
        raise ValueError(
            f"favicon must be a same-origin path when assets are local, got {favicon!r}"
        )


def _sri(data: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")


def _asset_check(directory: Path, provider: Provider) -> Callable[[], list[str]]:
    """A startup check that the directory holds the pinned files, unmodified."""

    def check() -> list[str]:
        problems: list[str] = []
        for asset in pages.PROVIDER_ASSETS[provider]:
            path = directory / asset.name
            try:
                data = path.read_bytes()
            except OSError as exc:
                problems.append(f"docs assets: cannot read {path}: {exc}")
                continue
            if _sri(data) != asset.sri:
                problems.append(
                    f"docs assets: {path} is not the pinned {provider} file "
                    f"({asset.url}); its hash does not match"
                )
        return problems

    return check


def mount_docs(
    app: DocsHost,
    prefix: str = "/docs",
    provider: Provider = "scalar",
    *,
    title: str | None = None,
    favicon: str | None = None,
    theme: Theme = "auto",
    assets: str | Path | None = None,
) -> None:
    """Serve the app's OpenAPI document at ``prefix`` in a viewer.

    ``provider`` picks Scalar, Swagger UI or Redoc. The viewer loads from a
    pinned CDN, or from ``assets`` (a directory holding the pinned files; see
    ``docs/openapi.md``), in which case the page is served with a policy that
    allows only the app's own origin and the files are checked against their
    pinned hashes when the app starts. ``theme`` is ``"auto"`` (follow the
    browser), ``"light"`` or ``"dark"``. The page registers one GET route at
    ``prefix`` (plus ``{prefix}/assets/*`` with ``assets``); the document itself
    stays at the app's ``openapi_url``.

    Raises ``ValueError`` for an invalid setting or an app without a document,
    and ``RuntimeError`` once the app is built. A route clash or a missing or
    modified asset file surfaces as ``StartupError`` when the app is built.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {PROVIDERS}, got {provider!r}")
    if theme not in THEMES:
        raise ValueError(f"theme must be one of {THEMES}, got {theme!r}")
    _check_prefix(prefix)
    openapi_url = app.openapi_url
    if openapi_url is None:
        raise ValueError("mount_docs needs the document: set openapi_url on the app")
    if favicon is not None:
        _check_favicon(favicon, offline=assets is not None)

    base = prefix.rstrip("/")
    directory = None if assets is None else Path(assets).resolve()
    files = None if directory is None else StaticFiles(directory)
    assets_prefix = None if directory is None else f"{base}/assets"
    page = _PAGES[provider](
        openapi_url,
        app.title if title is None else title,
        favicon=favicon,
        theme=theme,
        assets_prefix=assets_prefix,
    ).encode()
    pairs = [_HTML_TYPE] if assets is None else [_HTML_TYPE, _CSP]

    async def serve(c: Context) -> Response:
        return Response(200, page, MutableHeaders(list(pairs)))

    app.get(prefix, serve)
    if files is not None and directory is not None:
        app.get(f"{base}/assets/*", files)
        app.add_startup_check(_asset_check(directory, provider))
