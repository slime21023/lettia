"""Integration tests for Contract-layer WebSocket routes and Pre-Handshake Guards."""

import asyncio
from typing import Any, override

import msgspec
import pytest

from lettia import (
    App,
    Context,
    GuardSpec,
    RequestHead,
    WebSocket,
    op,
    public,
)
from lettia.core import Forbidden, Message, Unauthorized
from lettia.core.errors import StartupError
from lettia.testing import TestClient


class User(msgspec.Struct):
    id: int
    name: str


class ChatMessage(msgspec.Struct):
    text: str


auth_guard = GuardSpec[User]("auth_guard")


@auth_guard.use
async def verify_auth(head: RequestHead) -> User:
    auth = head.headers.get("authorization")
    if not auth:
        raise Unauthorized("Missing authorization header")
    if auth != "Bearer secret-token":
        raise Forbidden("Invalid bearer token")
    return User(id=42, name="Alice")


class ChatContract:
    @op.websocket("/chat/{room}", guard=public)
    async def public_chat(self, c: Context[None], ws: WebSocket) -> None:
        """Public chat room."""

    @op.websocket("/secure-chat", guard=auth_guard)
    async def secure_chat(self, c: Context[User], ws: WebSocket) -> None:
        """Guarded chat room."""


class ChatService(ChatContract):
    def __init__(self) -> None:
        self.entered = False

    @override
    async def public_chat(self, c: Context[None], ws: WebSocket) -> None:
        await ws.accept()
        room = c.path_params["room"]
        assert ws.path_params["room"] == room
        msg = await ws.receive_json(ChatMessage)
        await ws.send_json(ChatMessage(text=f"[{room}] {msg.text}"))
        await ws.close()

    @override
    async def secure_chat(self, c: Context[User], ws: WebSocket) -> None:
        self.entered = True
        await ws.accept()
        # Verify that principal was injected into Context by the Pre-Handshake Guard
        assert c.principal.id == 42
        assert c.principal.name == "Alice"
        await ws.send_text(f"Welcome, {c.principal.name}!")
        await ws.close()


@pytest.mark.asyncio
async def test_contract_websocket_public_flow() -> None:
    app = App(guards=[verify_auth])
    service = ChatService()
    app.mount(service)

    async with TestClient(app) as client:
        async with client.websocket_connect("/chat/general") as ws:
            assert ws.accepted
            await ws.send_json({"text": "Hello world!"})
            reply = await ws.receive_json(ChatMessage)
            assert reply.text == "[general] Hello world!"


@pytest.mark.asyncio
async def test_contract_websocket_pre_handshake_guard_success() -> None:
    app = App(guards=[verify_auth])
    service = ChatService()
    app.mount(service)

    async with TestClient(app) as client:
        async with client.websocket_connect(
            "/secure-chat",
            headers={"Authorization": "Bearer secret-token"},
        ) as ws:
            assert ws.accepted
            assert service.entered is True
            msg = await ws.receive_text()
            assert msg == "Welcome, Alice!"


@pytest.mark.asyncio
async def test_contract_websocket_pre_handshake_guard_unauthorized() -> None:
    app = App(guards=[verify_auth])
    service = ChatService()
    app.mount(service)

    async with TestClient(app) as client:
        # Connecting without authorization header
        async with client.websocket_connect("/secure-chat") as ws:
            assert not ws.accepted
            assert ws.response_status == 401
            assert b"Missing authorization header" in ws.response_body
            # Crucial: Handler was never entered!
            assert service.entered is False


@pytest.mark.asyncio
async def test_contract_websocket_pre_handshake_guard_forbidden() -> None:
    app = App(guards=[verify_auth])
    service = ChatService()
    app.mount(service)

    async with TestClient(app) as client:
        # Connecting with wrong token
        async with client.websocket_connect(
            "/secure-chat",
            headers={"Authorization": "Bearer wrong-token"},
        ) as ws:
            assert not ws.accepted
            assert ws.response_status == 403
            assert b"Invalid bearer token" in ws.response_body
            assert service.entered is False


def test_contract_websocket_missing_guard_implementation() -> None:
    missing_guard = GuardSpec[Any]("unbound_ws_guard")

    class UnboundContract:
        @op.websocket("/unbound", guard=missing_guard)
        async def unbound(self, c: Context[Any], ws: WebSocket) -> None:
            pass

    class UnboundService(UnboundContract):
        @override
        async def unbound(self, c: Context[Any], ws: WebSocket) -> None:
            pass

    app = App()
    app.mount(UnboundService())

    with pytest.raises(
        StartupError, match="guard 'unbound_ws_guard' has no implementation"
    ):
        app.build()


def test_contract_websocket_invalid_return_type_rejected() -> None:
    bad_op: Any = op.websocket

    class BadReturnContract:
        @bad_op("/bad-return", guard=public)
        async def bad_return(self, c: Context[None], ws: WebSocket) -> str:
            return "not allowed"

    class BadReturnService(BadReturnContract):
        @override
        async def bad_return(self, c: Context[None], ws: WebSocket) -> str:
            return "not allowed"

    app = App()
    app.mount(BadReturnService())

    with pytest.raises(
        StartupError, match="return type of a WebSocket operation must be None"
    ):
        app.build()


@pytest.mark.asyncio
async def test_contract_websocket_fallback_close_code_without_http_extension() -> None:
    app = App(guards=[verify_auth])
    service = ChatService()
    app.mount(service)
    app.build()

    to_app: asyncio.Queue[Message] = asyncio.Queue()
    from_app: asyncio.Queue[Message] = asyncio.Queue()

    scope: dict[str, Any] = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "path": "/secure-chat",
        "raw_path": b"/secure-chat",
        "headers": [],
        "extensions": {},
    }

    task = asyncio.create_task(app(scope, to_app.get, from_app.put))
    await to_app.put({"type": "websocket.connect"})

    close_msg = await from_app.get()
    assert close_msg["type"] == "websocket.close"
    assert close_msg["code"] == 4401
    await task
