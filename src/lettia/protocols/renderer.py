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
        html = self.templates.get(template_name, "")
        ctx = context or {}
        for k, v in ctx.items():
            html = html.replace(f"{{{{{k}}}}}", str(v))
        return TextResponse(
            text=html,
            status_code=status_code,
            media_type="text/html; charset=utf-8",
        )
