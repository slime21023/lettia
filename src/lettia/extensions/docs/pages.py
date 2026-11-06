"""HTML pages that show the OpenAPI document in an interactive viewer.

The viewers load from a CDN or, with ``assets``, from local files. Every file is
pinned to an exact version and carries a Subresource Integrity hash, so a
tampered CDN file is not executed and a tampered local file is not served.
Changing a version means recomputing its hash (``openssl dgst -sha384 -binary``
piped to ``openssl base64 -A``) from the exact bytes at the pinned URL.
"""

import html
import json
from dataclasses import dataclass
from typing import Literal

type Provider = Literal["scalar", "swagger", "redoc"]
type Theme = Literal["auto", "light", "dark"]

PROVIDERS: tuple[Provider, ...] = ("scalar", "swagger", "redoc")
THEMES: tuple[Theme, ...] = ("auto", "light", "dark")

SCALAR_VERSION = "1.60.0"
SWAGGER_UI_VERSION = "5.34.1"
REDOC_VERSION = "2.5.4"

# Offline pages are served with this policy; it keeps them from reaching any
# other origin, so the viewers cannot load fonts, telemetry or proxies.
OFFLINE_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
    "img-src 'self' data:; font-src 'self' data:; worker-src 'self' blob:; "
    "object-src 'none'; frame-src 'none'"
)


@dataclass(frozen=True, slots=True)
class Asset:
    """One pinned viewer file: its file name when served locally, URL and hash."""

    name: str
    url: str
    sri: str


_SWAGGER_BASE = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}"

SCALAR_JS = Asset(
    "standalone.js",
    f"https://cdn.jsdelivr.net/npm/@scalar/api-reference@{SCALAR_VERSION}"
    "/dist/browser/standalone.js",
    "sha384-3sxnxyp7pbU2/o4+gs4EbvQ4YKyF60pWDL2LW8SoFZNQBTSiPah2xcHpxsndZEgF",
)
SWAGGER_CSS = Asset(
    "swagger-ui.css",
    f"{_SWAGGER_BASE}/swagger-ui.css",
    "sha384-4c3vsE5f+trhFyHrFrLM1fFGUn531NIaC7dcqEWIJdoRQJ5CkJbSuSCwXLpaS1KF",
)
SWAGGER_JS = Asset(
    "swagger-ui-bundle.js",
    f"{_SWAGGER_BASE}/swagger-ui-bundle.js",
    "sha384-XFYN47VzPiDY6qPtxYKg0nqUeW/upo20xf1j2SUE7gT/i35FJB4cuS/71Iegzk8Z",
)
REDOC_JS = Asset(
    "redoc.standalone.js",
    f"https://cdn.jsdelivr.net/npm/redoc@{REDOC_VERSION}/bundles/redoc.standalone.js",
    "sha384-w447zOpYfw/1Tv/5AK9NfHTlQIqE3RVR6KY62jCyy9zNDgO64cMwGGP1Fj0zJVf5",
)

# The files a provider needs when it is served from a local directory.
PROVIDER_ASSETS: dict[Provider, tuple[Asset, ...]] = {
    "scalar": (SCALAR_JS,),
    "swagger": (SWAGGER_CSS, SWAGGER_JS),
    "redoc": (REDOC_JS,),
}

_SCRIPT_REPLACEMENTS = (
    ("<", "\\u003c"),
    (">", "\\u003e"),
    ("&", "\\u0026"),
    ("\u2028", "\\u2028"),
    ("\u2029", "\\u2029"),
)


def _js_value(value: object) -> str:
    """A JavaScript literal that cannot end the surrounding script tag."""
    literal = json.dumps(value)
    for char, escape in _SCRIPT_REPLACEMENTS:
        literal = literal.replace(char, escape)
    return literal


def _attr(value: str) -> str:
    return html.escape(value, quote=True)


def _reference(attr: str, asset: Asset, assets_prefix: str | None) -> str:
    """``src``/``href`` of the asset, always with its integrity check."""
    if assets_prefix is None:
        url = f'{attr}="{asset.url}"'
        return f'{url} integrity="{asset.sri}" crossorigin="anonymous"'
    return f'{attr}="{_attr(assets_prefix)}/{asset.name}" integrity="{asset.sri}"'


def _script(asset: Asset, assets_prefix: str | None) -> str:
    return f"<script {_reference('src', asset, assets_prefix)}></script>"


def _stylesheet(asset: Asset, assets_prefix: str | None) -> str:
    return f'<link rel="stylesheet" {_reference("href", asset, assets_prefix)}>'


def _head(title: str, favicon: str | None) -> str:
    icon = f'\n<link rel="icon" href="{_attr(favicon)}">' if favicon else ""
    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>{icon}"""


def scalar_page(
    openapi_url: str,
    title: str,
    *,
    favicon: str | None = None,
    theme: Theme = "auto",
    assets_prefix: str | None = None,
) -> str:
    config: dict[str, object] = {"withDefaultFonts": False}
    if theme != "auto":
        config["forceDarkModeState"] = theme
        config["hideDarkModeToggle"] = True
    return f"""<!doctype html>
<html lang="en">
<head>
{_head(title, favicon)}
</head>
<body>
<script id="api-reference" data-url="{_attr(openapi_url)}" data-configuration="{_attr(json.dumps(config))}"></script>
{_script(SCALAR_JS, assets_prefix)}
</body>
</html>
"""


def swagger_ui_page(
    openapi_url: str,
    title: str,
    *,
    favicon: str | None = None,
    theme: Theme = "auto",
    assets_prefix: str | None = None,
) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
{_head(title, favicon)}
{_stylesheet(SWAGGER_CSS, assets_prefix)}
</head>
<body>
<div id="swagger-ui"></div>
{_script(SWAGGER_JS, assets_prefix)}
<script>
(function () {{
  var theme = {_js_value(theme)};
  var root = document.documentElement;
  var query = window.matchMedia("(prefers-color-scheme: dark)");
  function apply() {{
    root.classList.toggle("dark-mode", theme === "auto" ? query.matches : theme === "dark");
  }}
  apply();
  if (theme === "auto") query.addEventListener("change", apply);
  window.ui = SwaggerUIBundle({{url: {_js_value(openapi_url)}, dom_id: "#swagger-ui", validatorUrl: null}});
}})();
</script>
</body>
</html>
"""


# background, foreground, panel, muted, primary
_REDOC_LIGHT = ("#ffffff", "#111827", "#f3f4f6", "#4b5563", "#2563eb")
_REDOC_DARK = ("#111827", "#e5e7eb", "#1f2937", "#9ca3af", "#60a5fa")
_REDOC_PALETTE_KEYS = ("background", "foreground", "panel", "muted", "primary")
_REDOC_PALETTES = {
    "light": dict(zip(_REDOC_PALETTE_KEYS, _REDOC_LIGHT, strict=True)),
    "dark": dict(zip(_REDOC_PALETTE_KEYS, _REDOC_DARK, strict=True)),
}


def redoc_page(
    openapi_url: str,
    title: str,
    *,
    favicon: str | None = None,
    theme: Theme = "auto",
    assets_prefix: str | None = None,
) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
{_head(title, favicon)}
</head>
<body style="margin: 0">
<div id="redoc"></div>
{_script(REDOC_JS, assets_prefix)}
<script>
(function () {{
  var theme = {_js_value(theme)};
  var url = {_js_value(openapi_url)};
  var palettes = {_js_value(_REDOC_PALETTES)};
  var font = "system-ui, -apple-system, 'Segoe UI', sans-serif";
  var query = window.matchMedia("(prefers-color-scheme: dark)");
  var element = document.getElementById("redoc");
  var current = null;
  var style = document.createElement("style");
  document.head.appendChild(style);
  function options(p) {{
    return {{theme: {{
      colors: {{text: {{primary: p.foreground, secondary: p.muted}}, primary: {{main: p.primary}}}},
      typography: {{
        fontFamily: font,
        headings: {{fontFamily: font}},
        code: {{color: p.foreground, backgroundColor: p.panel}},
        links: {{color: p.primary, visited: p.primary, hover: p.primary}}
      }},
      sidebar: {{backgroundColor: p.panel, textColor: p.foreground}},
      rightPanel: {{backgroundColor: p.panel, textColor: p.foreground}},
      codeBlock: {{backgroundColor: p.panel}},
      schema: {{nestedBackground: p.panel}}
    }}}};
  }}
  function render() {{
    var name = theme === "auto" ? (query.matches ? "dark" : "light") : theme;
    if (name === current) return;
    current = name;
    var p = palettes[name];
    document.body.style.background = p.background;
    document.body.style.color = p.foreground;
    document.documentElement.style.colorScheme = name;
    // Redoc hardcodes dark colors on its section labels; the theme cannot reach it.
    style.textContent = "#redoc h5, #redoc h5 span {{ color: " + p.muted + " !important; }}";
    if (typeof Redoc.destroy === "function") Redoc.destroy(element);
    Redoc.init(url, options(p), element);
  }}
  render();
  if (theme === "auto") query.addEventListener("change", render);
}})();
</script>
</body>
</html>
"""
