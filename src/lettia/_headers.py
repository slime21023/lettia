import re

_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9a-zA-Z-]+")
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*")


def validate_header_component(value: str, component: str) -> None:
    if "\r" in value or "\n" in value:
        raise ValueError(f"HTTP header {component}s cannot contain newlines")
    try:
        value.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise ValueError(f"HTTP header {component}s must be Latin-1 encodable") from exc
    if component == "name":
        if _TOKEN.fullmatch(value) is None:
            raise ValueError("HTTP header names must be nonempty ASCII tokens")
    elif any(ord(char) < 32 and char != "\t" or ord(char) == 127 for char in value):
        raise ValueError("HTTP header values cannot contain control characters")


def header_keys(headers: dict[str, str], name: str) -> list[str]:
    return [key for key in headers if key.lower() == name.lower()]


def cookie_value(
    key: str,
    value: str,
    max_age: int | None = None,
    path: str = "/",
    domain: str | None = None,
    secure: bool = False,
    httponly: bool = False,
    samesite: str = "lax",
    expires: str | None = None,
) -> str:
    for attribute in (key, value, path, domain, samesite, expires):
        if attribute is not None:
            validate_header_component(attribute, "value")

    if _TOKEN.fullmatch(key) is None:
        raise ValueError("Cookie names must be nonempty ASCII tokens")
    if _COOKIE_VALUE.fullmatch(value) is None:
        raise ValueError("Cookie values must contain only cookie-octet characters")
    for attribute in (path, domain, samesite, expires):
        if attribute is not None and any(
            char == ";" or not 32 <= ord(char) < 127 for char in attribute
        ):
            raise ValueError("Cookie attributes must be ASCII without semicolons")
    if samesite.lower() not in ("", "lax", "strict", "none"):
        raise ValueError("Cookie SameSite must be lax, strict, none, or empty")
    prefix = key.lower()
    if prefix.startswith(("__host-", "__secure-")) and not secure:
        raise ValueError("Prefixed cookies require Secure")
    if prefix.startswith("__host-") and (path != "/" or domain):
        raise ValueError("__Host- cookies require Path=/ and no Domain")

    cookie_val = f"{key}={value}; Path={path}"
    if max_age is not None:
        cookie_val += f"; Max-Age={max_age}"
    if domain:
        cookie_val += f"; Domain={domain}"
    if expires:
        cookie_val += f"; Expires={expires}"
    if secure:
        cookie_val += "; Secure"
    if httponly:
        cookie_val += "; HttpOnly"
    if samesite:
        cookie_val += f"; SameSite={samesite}"

    return cookie_val


def build_headers(
    headers: dict[str, str],
    media_type: str,
    status_code: int,
    body_length: int,
    streaming: bool,
    head_only: bool,
) -> list[tuple[bytes, bytes]]:
    raw_headers: list[tuple[bytes, bytes]] = []
    has_content_type = False
    content_length: str | None = None

    for k, v in headers.items():
        validate_header_component(k, "name")
        k_bytes = k.lower().encode("latin-1")
        if k_bytes == b"content-type":
            has_content_type = True
        if k_bytes == b"content-length":
            if status_code in (204, 304):
                continue
            if content_length is not None:
                raise ValueError(
                    "Response cannot contain multiple Content-Length headers"
                )
            if re.fullmatch(r"[0-9]+", v) is None:
                raise ValueError("Content-Length must contain only ASCII digits")
            content_length = v

        if k_bytes == b"set-cookie":
            for cookie_line in v.split("\n"):
                validate_header_component(cookie_line, "value")
                raw_headers.append((b"set-cookie", cookie_line.encode("latin-1")))
        else:
            validate_header_component(v, "value")
            raw_headers.append((k_bytes, v.encode("latin-1")))

    if not has_content_type and media_type:
        validate_header_component(media_type, "value")
        raw_headers.append((b"content-type", media_type.encode("latin-1")))

    if not streaming and status_code not in (204, 304):
        expected_length = str(body_length)
        # HEAD may describe a GET representation without constructing its body.
        # Compare decimal values without int()'s digit limit on long headers.
        if (
            not head_only
            and content_length is not None
            and (content_length.lstrip("0") or "0") != expected_length
        ):
            raise ValueError("Content-Length does not match the response body")
        if content_length is None:
            raw_headers.append((b"content-length", expected_length.encode("latin-1")))

    return raw_headers
