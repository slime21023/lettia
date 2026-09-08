import asyncio
import runpy
from pathlib import Path

from lettia.testing import TestClient

ROOT = Path(__file__).parent.parent


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

    for handler in app.on_startup:
        asyncio.run(handler())

    client = TestClient(app)
    assert client.get("/users/1").json() == {"id": "1", "name": "Ada"}
    assert client.get("/users/404").status_code == 404

    for handler in app.on_shutdown:
        asyncio.run(handler())

    assert events == ["start", "close"]
