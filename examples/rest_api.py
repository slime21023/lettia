from typing import Any

from attrs import define

from lettia import App, Context, JsonResponse
from lettia.middleware import body_limit, cors, recover, request_id, request_logger
from lettia.protocols import AttrsBinder, CallableValidator


# Data models using attrs with slots=True
@define(slots=True)
class CreateUserPayload:
    name: str
    email: str
    age: int = 18


# Validator
user_validator = CallableValidator(
    lambda u: len(u.name) >= 2 and "@" in u.email and u.age >= 18
)

# App instance
app = App()

# Apply global middlewares
app.use(
    recover(),
    request_logger(),
    cors(allow_origins=["*"]),
    request_id(),
    body_limit(max_bytes=1024 * 1024),  # 1MB limit
)

# In-memory database
db: dict[str, dict[str, Any]] = {
    "1": {"id": "1", "name": "Alice", "email": "alice@example.com", "age": 25},
    "2": {"id": "2", "name": "Bob", "email": "bob@example.com", "age": 30},
}

# Router Grouping
v1 = app.group("/api/v1")
users = v1.group("/users")


@users.get("/", name="list_users")
def list_users(ctx: Context) -> dict[str, Any]:
    return {
        "data": list(db.values()),
        "request_id": ctx.state.get("request_id"),
    }


@users.get("/:id", name="get_user")
def get_user(ctx: Context) -> dict[str, Any]:
    user_id = ctx.path_params.get("id", "")
    if user_id not in db:
        ctx.abort(404, f"User {user_id} not found")
    return {"data": db[user_id]}


@users.post("/", name="create_user")
async def create_user(ctx: Context) -> JsonResponse:
    binder = AttrsBinder()
    payload = await binder.bind(ctx, CreateUserPayload)

    # Validate
    user_validator.validate(payload)

    new_id = str(len(db) + 1)
    new_user = {
        "id": new_id,
        "name": payload.name,
        "email": payload.email,
        "age": payload.age,
    }
    db[new_id] = new_user

    return JsonResponse(
        {"data": new_user, "message": "User created successfully"},
        status_code=201,
    )


if __name__ == "__main__":
    import uvicorn

    print("Starting Lettia REST API example server on http://127.0.0.1:8000 ...")
    uvicorn.run(app, host="127.0.0.1", port=8000)
