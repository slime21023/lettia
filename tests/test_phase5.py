from attrs import define

from lettia import App, Context, JsonResponse
from lettia.testing import TestClient


@define(slots=True)
class UserPayload:
    name: str
    age: int = 18


def test_smart_bind_and_background_task() -> None:
    background_ran = False
    app = App()

    def set_bg_flag(name: str) -> None:
        nonlocal background_ran
        background_ran = True

    @app.post("/users")
    async def create_user(ctx: Context) -> JsonResponse:
        user = await ctx.bind(UserPayload)
        ctx.add_background_task(set_bg_flag, user.name)
        return JsonResponse({"name": user.name, "age": user.age}, status_code=201)

    client = TestClient(app)
    response = client.post("/users?age=20", json={"name": "Charlie"})

    assert response.status_code == 201
    assert response.json() == {"name": "Charlie", "age": 20}
    assert background_ran
