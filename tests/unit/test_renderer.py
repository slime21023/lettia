from __future__ import annotations

from html.parser import HTMLParser
from typing import override

import pytest
from hypothesis import example, given

from lettia.protocols import (
    SimpleHTMLRenderer,
)
from tests.support.strategies import TEXT


class ParsedHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.text: list[str] = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)

    @override
    def handle_data(self, data: str) -> None:
        self.text.append(data)


@pytest.mark.contract("RENDER-ESCAPE")
@given(value=TEXT)
@example(value="<script>alert(1)</script>&\"'")
@example(value="{{other}}")
def test_renderer_preserves_text_without_interpreting_input(value: str) -> None:
    response = SimpleHTMLRenderer({"page": "<p>{{value}}</p>"}).render(
        "page", {"value": value, "other": "replaced"}
    )
    parsed = ParsedHTML()
    parsed.feed(response.body.decode())
    parsed.close()
    assert parsed.tags == ["p"]
    assert "".join(parsed.text) == value
