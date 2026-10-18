"""Startup checks 4 to 7 and the shape of what ``mount`` accepts."""

from collections.abc import Callable, Sequence
from typing import Any, Protocol, override

import pytest

from lettia import App, Context, GuardSpec, NoContent, Ok, op, public
from lettia.contract import MsgspecCodec
from lettia.core import StartupError


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
