"""HTML pages that show the OpenAPI document in an interactive viewer.

The viewers are loaded from a CDN. Every file is pinned to an exact version and
carries a Subresource Integrity hash, so a tampered CDN file is not executed.
Changing a version means recomputing its hash (``openssl dgst -sha384 -binary``
piped to ``openssl base64 -A``).
"""

import html
import json

SWAGGER_UI_VERSION = "5.17.14"
SCALAR_VERSION = "1.60.0"

_SWAGGER_BASE = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}"
_SWAGGER_CSS_SRI = (
    "sha384-wxLW6kwyHktdDGr6Pv1zgm/VGJh99lfUbzSn6HNHBENZlCN7W602k9VkGdxuFvPn"
)
_SWAGGER_JS_SRI = (
    "sha384-wmyclcVGX/WhUkdkATwhaK1X1JtiNrr2EoYJ+diV3vj4v6OC5yCeSu+yW13SYJep"
)
_SCALAR_JS = (
    f"https://cdn.jsdelivr.net/npm/@scalar/api-reference@{SCALAR_VERSION}"
    "/dist/browser/standalone.js"
)
_SCALAR_JS_SRI = (
    "sha384-3sxnxyp7pbU2/o4+gs4EbvQ4YKyF60pWDL2LW8SoFZNQBTSiPah2xcHpxsndZEgF"
)


def _js_string(value: str) -> str:
    """A JavaScript string literal that cannot end the surrounding script tag."""
    literal = json.dumps(value)
    for char, escape in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        (" ", "\\u2028"),
        (" ", "\\u2029"),
    ):
        literal = literal.replace(char, escape)
    return literal


def swagger_ui_page(openapi_url: str, title: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="{_SWAGGER_BASE}/swagger-ui.css" integrity="{_SWAGGER_CSS_SRI}" crossorigin="anonymous">
</head>
<body>
<div id="swagger-ui"></div>
<script src="{_SWAGGER_BASE}/swagger-ui-bundle.js" integrity="{_SWAGGER_JS_SRI}" crossorigin="anonymous"></script>
<script>
window.ui = SwaggerUIBundle({{url: {_js_string(openapi_url)}, dom_id: "#swagger-ui"}});
</script>
</body>
</html>
"""


def scalar_page(openapi_url: str, title: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
</head>
<body>
<script id="api-reference" data-url="{html.escape(openapi_url, quote=True)}"></script>
<script src="{_SCALAR_JS}" integrity="{_SCALAR_JS_SRI}" crossorigin="anonymous"></script>
</body>
</html>
"""
