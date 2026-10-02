import html
import re
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from lettia.response import Response, TextResponse


@runtime_checkable
class Renderer(Protocol):
    def render(
        self,
        template_name: str,
        context: Mapping[str, object] | None = None,
        status_code: int = 200,
    ) -> Response: ...


class SimpleHTMLRenderer:
    def __init__(self, templates: dict[str, str] | None = None) -> None:
        self.templates: dict[str, str] = templates or {}

    def render(
        self,
        template_name: str,
        context: Mapping[str, object] | None = None,
        status_code: int = 200,
    ) -> Response:
        template = self.templates.get(template_name, "")
        ctx = context or {}

        def replace(match: re.Match[str]) -> str:
            return html.escape(str(ctx[match[1]])) if match[1] in ctx else match[0]

        rendered = re.sub(r"\{\{([^{}]+)\}\}", replace, template)
        return TextResponse(
            text=rendered,
            status_code=status_code,
            media_type="text/html; charset=utf-8",
        )
