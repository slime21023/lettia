import json
from collections.abc import AsyncIterable
from typing import Any

from attrs import define, field


@define(slots=True)
class Response:
    status_code: int = 200
    headers: dict[str, str] = field(factory=dict)
    body: bytes = b""
    media_type: str = "text/plain; charset=utf-8"
    async_body: AsyncIterable[bytes] | None = None

    def set_header(self, name: str, value: str) -> None:
        if "\r" in name or "\n" in name or "\r" in value or "\n" in value:
            raise ValueError("HTTP header names and values cannot contain newlines")
        self.headers[name.lower()] = value

    def set_cookie(
        self,
        key: str,
        value: str,
        max_age: int | None = None,
        path: str = "/",
        domain: str | None = None,
        secure: bool = False,
        httponly: bool = False,
        samesite: str = "lax",
        expires: str | None = None,
    ) -> None:
        if any("\r" in value or "\n" in value for value in (key, value, path)):
            raise ValueError("Cookie attributes cannot contain newlines")
        if domain is not None and ("\r" in domain or "\n" in domain):
            raise ValueError("Cookie attributes cannot contain newlines")
        if samesite and ("\r" in samesite or "\n" in samesite):
            raise ValueError("Cookie attributes cannot contain newlines")

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

        # Store cookies as multi-headers
        # In ASGI, set-cookie headers can be sent repeatedly
        if "set-cookie" in self.headers:
            self.headers["set-cookie"] += f"\n{cookie_val}"
        else:
            self.headers["set-cookie"] = cookie_val

    def delete_cookie(
        self,
        key: str,
        path: str = "/",
        domain: str | None = None,
    ) -> None:
        self.set_cookie(key, "", max_age=0, path=path, domain=domain)


@define(slots=True)
class TextResponse(Response):
    def __init__(
        self,
        text: str,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        media_type: str = "text/plain; charset=utf-8",
    ) -> None:
        hdr = headers.copy() if headers else {}
        super().__init__(
            status_code=status_code,
            headers=hdr,
            body=text.encode("utf-8"),
            media_type=media_type,
        )


@define(slots=True)
class JsonResponse(Response):
    def __init__(
        self,
        data: Any,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        media_type: str = "application/json",
    ) -> None:
        hdr = headers.copy() if headers else {}
        json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
        super().__init__(
            status_code=status_code,
            headers=hdr,
            body=json_bytes,
            media_type=media_type,
        )


@define(slots=True)
class StreamResponse(Response):
    def __init__(
        self,
        generator: AsyncIterable[bytes],
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        media_type: str = "application/octet-stream",
    ) -> None:
        hdr = headers.copy() if headers else {}
        super().__init__(
            status_code=status_code,
            headers=hdr,
            body=b"",
            media_type=media_type,
            async_body=generator,
        )


@define(slots=True)
class ResponseWriter:
    send: Any
    head_only: bool = False
    committed: bool = field(default=False, init=False)

    async def write(self, response: Response) -> None:
        if self.committed:
            return

        raw_headers: list[tuple[bytes, bytes]] = []
        has_content_type = False

        for k, v in response.headers.items():
            k_bytes = k.lower().encode("latin-1")
            if k_bytes == b"content-type":
                has_content_type = True
            # Handle multiple set-cookie lines separated by \n
            if k_bytes == b"set-cookie":
                for cookie_line in v.split("\n"):
                    raw_headers.append((b"set-cookie", cookie_line.encode("latin-1")))
            else:
                raw_headers.append((k_bytes, v.encode("latin-1")))

        if not has_content_type and response.media_type:
            raw_headers.append((b"content-type", response.media_type.encode("latin-1")))

        if response.async_body is None:
            raw_headers.append(
                (b"content-length", str(len(response.body)).encode("latin-1"))
            )

        await self.send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": raw_headers,
            }
        )
        self.committed = True

        if self.head_only:
            await self.send(
                {
                    "type": "http.response.body",
                    "body": b"",
                    "more_body": False,
                }
            )
        elif response.async_body is not None:
            async for chunk in response.async_body:
                await self.send(
                    {
                        "type": "http.response.body",
                        "body": chunk,
                        "more_body": True,
                    }
                )
            await self.send(
                {
                    "type": "http.response.body",
                    "body": b"",
                    "more_body": False,
                }
            )
        else:
            await self.send(
                {
                    "type": "http.response.body",
                    "body": response.body,
                    "more_body": False,
                }
            )


def normalize_response(result: Any) -> Response:
    if isinstance(result, Response):
        return result
    if isinstance(result, str):
        return TextResponse(result)
    if isinstance(result, bytes):
        return Response(body=result, media_type="application/octet-stream")
    if isinstance(result, (dict, list)):
        return JsonResponse(result)
    if isinstance(result, tuple) and len(result) >= 2:
        body_part, status_part = result[0], result[1]
        headers_part = (
            result[2] if len(result) >= 3 and isinstance(result[2], dict) else {}
        )
        resp = normalize_response(body_part)
        resp.status_code = int(status_part)
        for k, v in headers_part.items():
            resp.set_header(k, v)
        return resp

    return TextResponse(str(result))
