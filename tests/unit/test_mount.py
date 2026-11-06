"""Startup checks 4 to 7 and the shape of what ``mount`` accepts."""

from collections.abc import Callable, Sequence
from typing import Annotated, Any, Protocol, override

import pytest

from lettia import (
    App,
    Context,
    Created,
    GuardSpec,
    HeaderName,
    NoContent,
    Ok,
    WebSocket,
    op,
    public,
)
from lettia.contract import MsgspecCodec
from lettia.core import Context as CoreContext
from lettia.core import (
    Forbidden,
    Handler,
    RequestHead,
    Response,
    StartupError,
)
from lettia.testing import TestClient


class First(Protocol):
    @op.get("/first", guard=public)
    async def ping(self, c: Context[None], req: None) -> NoContent: ...

    @op.get("/shared", guard=public)
    async def shared(self, c: Context[None], req: None) -> NoContent: ...


class Second(Protocol):
    @op.get("/second", guard=public)
    async def ping(self, c: Context[None], req: None) -> Ok[int]: ...


class Third(Protocol):
    @op.get("/shared", guard=public)
    async def other(self, c: Context[None], req: None) -> NoContent: ...


class Plain:
    async def anything(self) -> None: ...


class Complete(First):
    async def ping(self, c: Context[None], req: None) -> NoContent:
        return NoContent()

    async def shared(self, c: Context[None], req: None) -> NoContent:
        return NoContent()


def make(cls: type) -> object:
    """Instantiate without a static check: the point is the runtime answer."""
    return cls()


def problems_of(app: App) -> list[str]:
    with pytest.raises(StartupError) as caught:
        app.build()
    return caught.value.problems


def test_mount_of_a_complete_implementation_builds() -> None:
    app = App()
    app.mount(Complete())

    app.build()


def test_mount_reports_a_contract_method_the_implementation_does_not_override() -> None:
    class Incomplete(First):
        async def ping(self, c: Context[None], req: None) -> NoContent:
            return NoContent()

    app = App()
    app.mount(make(Incomplete))

    [problem] = problems_of(app)

    assert "Incomplete" in problem
    assert "First.shared" in problem
    assert "implement" in problem


def test_mount_reports_an_implementation_method_that_is_not_async() -> None:
    class Blocking(First):
        def ping(self, c: Context[None], req: None) -> NoContent:  # type: ignore[override]
            return NoContent()

        async def shared(self, c: Context[None], req: None) -> NoContent:
            return NoContent()

    app = App()
    app.mount(Blocking())

    [problem] = problems_of(app)

    assert "async" in problem
    assert "Blocking.ping" in problem


def test_mount_reports_an_object_that_implements_no_contract() -> None:
    app = App()
    app.mount(Plain())

    [problem] = problems_of(app)

    assert "Plain" in problem
    assert "contract" in problem


def test_mount_reports_duplicate_operation_ids_across_contracts() -> None:
    class Both(First, Second):
        async def ping(self, c: Context[None], req: None) -> NoContent:  # type: ignore[override]
            return NoContent()

        async def shared(self, c: Context[None], req: None) -> NoContent:
            return NoContent()

    app = App()
    app.mount(Both())

    problems = problems_of(app)

    assert any("operationId 'ping'" in p for p in problems)


def test_mount_operation_ids_are_checked_across_separate_mounts() -> None:
    class SecondImpl(Second):
        async def ping(self, c: Context[None], req: None) -> Ok[int]:
            return Ok(1)

    app = App()
    app.mount(Complete())
    app.group("/v2").mount(SecondImpl())

    problems = problems_of(app)

    assert any("operationId 'ping'" in p for p in problems)


def test_mount_reports_routes_that_collide_at_startup() -> None:
    class ThirdImpl(Third):
        async def other(self, c: Context[None], req: None) -> NoContent:
            return NoContent()

    app = App()
    app.mount(Complete())
    app.mount(ThirdImpl())

    problems = problems_of(app)

    assert any("GET /shared" in p for p in problems)


def test_mount_lists_problems_from_every_mount_together() -> None:
    class Incomplete(First):
        async def ping(self, c: Context[None], req: None) -> NoContent:
            return NoContent()

    app = App()
    app.mount(make(Incomplete))
    app.mount(Plain())

    assert len(problems_of(app)) == 2


def test_mount_reports_malformed_contracts_with_their_location() -> None:
    class Broken(Protocol):
        @op.get("/items/{item_id}", guard=public)
        async def get_item(self, c: Context[None], req: None) -> NoContent: ...

    class BrokenImpl(Broken):
        async def get_item(self, c: Context[None], req: None) -> NoContent:
            return NoContent()

    app = App()
    app.mount(BrokenImpl())

    [problem] = problems_of(app)

    assert "Broken.get_item:" in problem
    assert "item_id" in problem


def test_mount_reports_a_guard_nobody_implemented() -> None:
    class Admin: ...

    class Guarded(Protocol):
        @op.get("/admin", guard=GuardSpec[Admin]("admin"))
        async def admin_only(self, c: Context[Admin], req: None) -> NoContent: ...

    class GuardedImpl(Guarded):
        async def admin_only(self, c: Context[Admin], req: None) -> NoContent:
            return NoContent()

    app = App()
    app.mount(GuardedImpl())

    [problem] = problems_of(app)

    assert "Guarded.admin_only" in problem
    assert "'admin'" in problem
    assert "App(guards=" in problem


def test_mount_after_the_app_is_built_is_rejected() -> None:
    app = App()
    app.build()

    with pytest.raises(RuntimeError, match="built"):
        app.mount(Complete())


def test_group_of_a_group_is_still_a_lettia_group_with_mount() -> None:
    app = App()
    nested: Any = app.group("/a").group("/b")

    nested.mount(Complete())

    app.build()


def test_app_codec_is_used_by_mounted_operations_and_openapi() -> None:
    class RecordingCodec(MsgspecCodec):
        def __init__(self) -> None:
            self.encode_builds = 0
            self.schema_calls = 0

        @override
        def build_encoder(self) -> Callable[[object], bytes]:
            self.encode_builds += 1
            return super().build_encoder()

        @override
        def json_schema(
            self, types: Sequence[Any], *, ref_template: str
        ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
            self.schema_calls += 1
            return super().json_schema(types, ref_template=ref_template)

    class SecondImpl(Second):
        async def ping(self, c: Context[None], req: None) -> Ok[int]:
            return Ok(1)

    custom_codec = RecordingCodec()
    app = App(codec=custom_codec)
    app.group("/v1").mount(SecondImpl())

    app.openapi()

    assert custom_codec.encode_builds >= 2
    assert custom_codec.schema_calls == 1


async def test_mount_closes_uploaded_files_after_handler() -> None:
    import msgspec

    from lettia import File, UploadFile
    from lettia.testing import TestClient

    class FileReq(msgspec.Struct, kw_only=True):
        avatar: File[UploadFile]

    class FileContract(Protocol):
        @op.post("/upload", guard=public)
        async def upload(self, c: Context[None], req: FileReq) -> Ok[str]: ...

    captured: list[UploadFile] = []

    class FileImpl(FileContract):
        async def upload(self, c: Context[None], req: FileReq) -> Ok[str]:
            captured.append(req.avatar)
            return Ok("ok")

    app = App()
    app.mount(FileImpl())

    async with TestClient(app) as client:
        resp = await client.post(
            "/upload",
            files={"avatar": ("avatar.png", b"binary-data", "image/png")},
        )
        assert resp.status_code == 200

    assert len(captured) == 1
    assert captured[0]._file.closed


class _BadHeaderReply(Created[str], kw_only=True):
    token: Annotated[str, HeaderName("Bad Name")]


class _BadHeaderApi(Protocol):
    @op.get("/bad-header", guard=public)
    async def bad(self, c: Context[None], req: None) -> _BadHeaderReply: ...


class _BadHeaderImpl(_BadHeaderApi):
    async def bad(self, c: Context[None], req: None) -> _BadHeaderReply:
        return _BadHeaderReply("x", token="y")


class _ObjectBodyApi(Protocol):
    @op.get("/object-body", guard=public)
    async def body(self, c: Context[None], req: None) -> Ok[dict[str, object]]: ...


class _ObjectBodyImpl(_ObjectBodyApi):
    async def body(self, c: Context[None], req: None) -> Ok[dict[str, object]]:
        return Ok({})


def test_mount_collects_an_invalid_response_header_name_as_a_startup_problem() -> None:
    app = App()
    app.mount(_BadHeaderImpl())  # must not raise: problems surface at startup

    with pytest.raises(StartupError) as caught:
        app.build()

    assert "'Bad Name'" in str(caught.value)


def test_a_body_type_without_a_json_schema_is_a_startup_problem() -> None:
    app = App()
    app.mount(_ObjectBodyImpl())

    with pytest.raises(StartupError) as caught:
        app.build()

    assert "cannot generate a JSON schema" in str(caught.value)


# --- per-operation middleware -------------------------------------------------


_closed = GuardSpec[None]("closed")


async def _refuse(head: RequestHead) -> None:
    raise Forbidden("closed")


class _AccountApi(Protocol):
    @op.post("/login", guard=public)
    async def login(self, c: Context[None], req: None) -> NoContent: ...

    @op.get("/me", guard=public)
    async def me(self, c: Context[None], req: None) -> NoContent: ...

    @op.get("/vault", guard=_closed)
    async def vault(self, c: Context[None], req: None) -> NoContent: ...

    @op.websocket("/live", guard=public)
    async def live(self, c: Context[None], ws: WebSocket) -> None: ...


class _Account(_AccountApi):
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    @override
    async def login(self, c: Context[None], req: None) -> NoContent:
        self.trace.append("login")
        return NoContent()

    @override
    async def me(self, c: Context[None], req: None) -> NoContent:
        self.trace.append("me")
        return NoContent()

    @override
    async def vault(self, c: Context[None], req: None) -> NoContent:
        self.trace.append("vault")
        return NoContent()

    @override
    async def live(self, c: Context[None], ws: WebSocket) -> None:
        return None


def _tracer(name: str, trace: list[str]) -> Callable[[Handler], Handler]:
    def middleware(next_: Handler) -> Handler:
        async def run(c: CoreContext) -> Response:
            trace.append(f"{name}>")
            response = await next_(c)
            trace.append(f"<{name}:{response.status}")
            return response

        return run

    return middleware


def _guarded_app() -> App:
    return App(guards=[_closed.use(_refuse)])


def _account_app(trace: list[str], **mount_options: Any) -> App:
    app = _guarded_app()
    app.use(_tracer("app", trace))
    app.mount(_Account(trace), **mount_options)
    return app


async def test_mount_middleware_wraps_only_the_keyed_operation() -> None:
    trace: list[str] = []
    app = _account_app(trace, middleware={_AccountApi.login: [_tracer("login", trace)]})

    async with TestClient(app) as http:
        assert (await http.post("/login")).status_code == 204
        assert trace == ["app>", "login>", "login", "<login:204", "<app:204"]
        trace.clear()
        assert (await http.get("/me")).status_code == 204

    assert trace == ["app>", "me", "<app:204"]


async def test_mount_middleware_runs_outside_the_guard_and_sees_its_refusal() -> None:
    trace: list[str] = []
    app = _account_app(trace, middleware={_AccountApi.vault: [_tracer("vault", trace)]})

    async with TestClient(app) as http:
        res = await http.get("/vault")

    assert res.status_code == 403
    assert trace == ["app>", "vault>", "<vault:403", "<app:403"]


async def test_mount_middleware_applies_inside_group_middleware() -> None:
    trace: list[str] = []
    app = _guarded_app()
    group = app.group("/v1", _tracer("group", trace))
    group.mount(
        _Account(trace),
        middleware={_AccountApi.login: [_tracer("login", trace)]},
    )

    async with TestClient(app) as http:
        await http.post("/v1/login")

    assert trace == ["group>", "login>", "login", "<login:204", "<group:204"]


def test_mount_middleware_keyed_by_an_override_is_a_problem() -> None:
    app = _guarded_app()
    # Keyed by the implementation's override, not by the contract method.
    app.mount(
        _Account([]),
        middleware={_Account.login: [_tracer("x", [])]},
    )

    [problem] = problems_of(app)

    assert "_Account.login" in problem
    assert "contract method" in problem


def test_mount_middleware_cannot_be_set_on_a_websocket_operation() -> None:
    app = _guarded_app()
    app.mount(
        _Account([]),
        middleware={_AccountApi.live: [_tracer("x", [])]},
    )

    [problem] = problems_of(app)

    assert "_AccountApi.live" in problem
    assert "WebSocket" in problem


def test_mount_middleware_must_map_to_a_sequence_not_one_callable() -> None:
    app = _guarded_app()

    with pytest.raises(TypeError, match="sequence"):
        app.mount(
            _Account([]),
            middleware={_AccountApi.login: _tracer("x", [])},  # type: ignore[dict-item]
        )
