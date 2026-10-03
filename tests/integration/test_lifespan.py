import asyncio
import runpy
from pathlib import Path

import pytest

from lettia import App
from lettia.testing import TestClient
from tests.support.asgi import (
    LifespanSendEvent,
    lifespan_receive,
    lifespan_scope,
    lifespan_sender,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.contract("APP-LIFESPAN")
@pytest.mark.asyncio
async def test_app_lifespan() -> None:
    app = App()
    events: list[str] = []

    def startup() -> None:
        events.append("startup")

    def shutdown() -> None:
        events.append("shutdown")

    app.on_event("startup")(startup)
    app.on_event("shutdown")(shutdown)

    sent_messages: list[LifespanSendEvent] = []
    await app(
        lifespan_scope(),
        lifespan_receive([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]),
        lifespan_sender(sent_messages),
    )

    assert events == ["startup", "shutdown"]
    assert sent_messages[0]["type"] == "lifespan.startup.complete"
    assert sent_messages[1]["type"] == "lifespan.shutdown.complete"


@pytest.mark.contract("APP-LIFESPAN")
@pytest.mark.asyncio
async def test_app_lifespan_reports_startup_failure() -> None:
    app = App()

    def fail_startup() -> None:
        raise RuntimeError("startup failed")

    app.on_event("startup")(fail_startup)
    sent_messages: list[LifespanSendEvent] = []
    await app(
        lifespan_scope(),
        lifespan_receive([{"type": "lifespan.startup"}]),
        lifespan_sender(sent_messages),
    )
    assert sent_messages == [
        {"type": "lifespan.startup.failed", "message": "startup failed"}
    ]


@pytest.mark.contract("APP-LIFESPAN")
def test_composition_root_injects_services_and_owns_lifecycle() -> None:
    events: list[str] = []
    template = runpy.run_path(str(ROOT / "examples" / "composition_root.py"))
    services_type = template["Services"]
    create_app = template["create_app"]

    async def get_user_name(user_id: str) -> str | None:
        return {"1": "Ada"}.get(user_id)

    async def start() -> None:
        events.append("start")

    async def close() -> None:
        events.append("close")

    services = services_type(
        get_user_name=get_user_name,
        start=start,
        close=close,
    )
    app = create_app(services)

    sent_messages: list[LifespanSendEvent] = []
    asyncio.run(
        app(
            lifespan_scope(),
            lifespan_receive(
                [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
            ),
            lifespan_sender(sent_messages),
        )
    )

    client = TestClient(app)
    assert client.get("/users/1").json() == {"id": "1", "name": "Ada"}
    assert client.get("/users/404").status_code == 404

    assert events == ["start", "close"]
