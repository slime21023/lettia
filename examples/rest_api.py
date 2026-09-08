from attrs import define

from lettia import REQUEST_ID, App, Context, HTTPHandler, JsonResponse, JSONValue
from lettia.middleware import body_limit, cors, recover, request_id, request_logger
from lettia.protocols import AttrsBinder, CallableValidator


# Data models using attrs with slots=True
@define(slots=True)
class CreateUserPayload:
    name: str
    email: str
    age: int = 18


def validate_user(payload: CreateUserPayload) -> bool:
    return len(payload.name) >= 2 and "@" in payload.email and payload.age >= 18


user_validator = CallableValidator[CreateUserPayload](validate_user)

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
db: dict[str, dict[str, JSONValue]] = {
    "1": {"id": "1", "name": "Alice", "email": "alice@example.com", "age": 25},
    "2": {"id": "2", "name": "Bob", "email": "bob@example.com", "age": 30},
}

# Router Grouping
v1 = app.group("/api/v1")
users = v1.group("/users")


def list_users(ctx: Context) -> dict[str, JSONValue]:
    users = list[JSONValue](db.values())
    return {
        "data": users,
        "request_id": ctx.state.get(REQUEST_ID),
    }


def get_user(ctx: Context) -> dict[str, JSONValue]:
    user_id = ctx.path_params.get("id", "")
    if user_id not in db:
        ctx.abort(404, f"User {user_id} not found")
    return {"data": db[user_id]}


async def create_user(ctx: Context) -> JsonResponse:
    binder = AttrsBinder()
    payload = await binder.bind(ctx, CreateUserPayload)

    # Validate
    user_validator.validate(payload)

    new_id = str(len(db) + 1)
    new_user: dict[str, JSONValue] = {
        "id": new_id,
        "name": payload.name,
        "email": payload.email,
        "age": payload.age,
    }
    db[new_id] = new_user

    payload_document: dict[str, JSONValue] = {
        "data": new_user,
        "message": "User created successfully",
    }
    return JsonResponse(payload_document, status_code=201)


list_users_handler: HTTPHandler = list_users
get_user_handler: HTTPHandler = get_user
create_user_handler: HTTPHandler = create_user
users.add_route("GET", "/", list_users_handler, name="list_users")
users.add_route("GET", "/:id", get_user_handler, name="get_user")
users.add_route("POST", "/", create_user_handler, name="create_user")


if __name__ == "__main__":
    import uvicorn

    print("Starting Lettia REST API example server on http://127.0.0.1:8000 ...")
    uvicorn.run(app, host="127.0.0.1", port=8000)
