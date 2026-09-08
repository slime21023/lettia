import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import cast

import httpx

from lettia.app import App
from lettia.asgi import JSONValue

type QueryValue = str | int | float | bool | None


class TestClient:
    __test__ = False

    def __init__(self, app: App, base_url: str = "http://testserver") -> None:
        self.app: App = app
        self.base_url: str = base_url
        # HTTPX exposes an untyped ASGI callable alias; keep that bridge local.
        httpx_app = cast(Callable[..., Awaitable[None]], self.app)
        self._transport = httpx.ASGITransport(app=httpx_app)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        content: str | bytes | None = None,
        json: JSONValue | None = None,
        params: Mapping[str, QueryValue] | None = None,
    ) -> httpx.Response:
        async def _run() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=self._transport, base_url=self.base_url
            ) as client:
                return await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    cookies=cookies,
                    content=content,
                    json=json,
                    params=params,
                )

        return asyncio.run(_run())

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: Mapping[str, QueryValue] | None = None,
    ) -> httpx.Response:
        return self.request("GET", url, headers=headers, params=params)

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: JSONValue | None = None,
        content: str | bytes | None = None,
    ) -> httpx.Response:
        return self.request("POST", url, headers=headers, json=json, content=content)

    def put(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: JSONValue | None = None,
        content: str | bytes | None = None,
    ) -> httpx.Response:
        return self.request("PUT", url, headers=headers, json=json, content=content)

    def delete(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return self.request("DELETE", url, headers=headers)

    def patch(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: JSONValue | None = None,
    ) -> httpx.Response:
        return self.request("PATCH", url, headers=headers, json=json)
