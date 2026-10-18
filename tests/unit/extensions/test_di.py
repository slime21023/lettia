from typing import Protocol

import pytest
import rodi

from lettia import App, Context, NoContent, Ok, op, public
from lettia.extensions.di import (
    SCOPE_KEY,
    Container,
    DIMiddleware,
    Injected,
    current_scope,
)
from lettia.testing import TestClient

# --- Domain models and services for testing ---


class AppConfig:
    def __init__(self, env: str = "test") -> None:
        self.env = env


class AsyncDatabaseSession:
    def __init__(self) -> None:
        self.closed = False
        self.records: list[str] = ["alice", "bob"]

    async def aclose(self) -> None:
        self.closed = True


class SyncCacheClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


# --- Test 1: Injected descriptor outside scope raises RuntimeError ---


def test_injected_descriptor_outside_scope_raises_error() -> None:
    class DummyService:
        config: Injected[AppConfig] = Injected()

    service = DummyService()
    with pytest.raises(RuntimeError, match="outside of an active request scope"):
        _ = service.config


# --- Test 2: Injected descriptor within active scope resolves instance ---


def test_injected_descriptor_within_active_scope() -> None:
    container = Container()
    container.add_singleton(AppConfig, AppConfig(env="production"))
    provider = container.build_provider()

    class DummyService:
        config: Injected[AppConfig] = Injected()

    service = DummyService()

    with provider.create_scope() as scope:
        token = current_scope.set(scope)
        try:
            assert service.config.env == "production"
        finally:
            current_scope.reset(token)


# --- Test 3: Full End-to-End Lettia Contract API with DI ---


class UserApi(Protocol):
    @op.get("/users", guard=public)
    async def list_users(self, c: Context[None], req: None) -> Ok[list[str]]: ...

    @op.get("/env", guard=public)
    async def get_env(self, c: Context[None], req: None) -> Ok[str]: ...


class UserService(UserApi):
    # Declarative Property Injections
    db: Injected[AsyncDatabaseSession]
    cache: Injected[SyncCacheClient]

    # Constructor Injection for Singleton
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    async def list_users(self, c: Context[None], req: None) -> Ok[list[str]]:
        return Ok(self.db.records)

    async def get_env(self, c: Context[None], req: None) -> Ok[str]:
        return Ok(self.config.env)


@pytest.mark.asyncio
async def test_di_end_to_end_request_lifecycle() -> None:
    session_instances: list[AsyncDatabaseSession] = []
    cache_instances: list[SyncCacheClient] = []

    def make_db() -> AsyncDatabaseSession:
        db = AsyncDatabaseSession()
        session_instances.append(db)
        return db

    def make_cache() -> SyncCacheClient:
        cache = SyncCacheClient()
        cache_instances.append(cache)
        return cache

    container = Container()
    container.add_singleton(AppConfig, AppConfig(env="staging"))
    container.add_scoped_by_factory(make_db, AsyncDatabaseSession)
    container.add_scoped_by_factory(make_cache, SyncCacheClient)

    app = App()
    app.use(DIMiddleware(container))
    container.wire(app, [UserService])

    async with TestClient(app) as client:
        # Request 1: list_users
        resp1 = await client.get("/users")
        assert resp1.status_code == 200
        assert resp1.json() == ["alice", "bob"]

        # Verify scoped teardown happened
        assert len(session_instances) == 1
        assert session_instances[0].closed is True

        # Request 2: get_env
        resp2 = await client.get("/env")
        assert resp2.status_code == 200
        assert resp2.json() == "staging"

        # Request 3: second request to list_users gets a fresh scoped session
        resp3 = await client.get("/users")
        assert resp3.status_code == 200
        assert len(session_instances) == 2
        assert session_instances[1].closed is True
        assert session_instances[0] is not session_instances[1]


# --- Test 4: Exception during request still tears down scoped resources ---


class FailingApi(Protocol):
    @op.get("/error", guard=public)
    async def error(self, c: Context[None], req: None) -> NoContent: ...


class FailingService(FailingApi):
    db: Injected[AsyncDatabaseSession]

    async def error(self, c: Context[None], req: None) -> NoContent:
        _ = self.db.records
        raise RuntimeError("simulated crash inside handler")


@pytest.mark.asyncio
async def test_scoped_teardown_on_unhandled_exception() -> None:
    sessions: list[AsyncDatabaseSession] = []

    def make_db() -> AsyncDatabaseSession:
        db = AsyncDatabaseSession()
        sessions.append(db)
        return db

    container = Container()
    container.add_scoped_by_factory(make_db, AsyncDatabaseSession)

    app = App()
    app.use(DIMiddleware(container))
    container.wire(app, [FailingService])

    async with TestClient(app) as client:
        resp = await client.get("/error")
        assert resp.status_code == 500

        # Scoped DB session must be closed despite the 500 error!
        assert len(sessions) == 1
        assert sessions[0].closed is True


# --- Test 5: Explicit access via SCOPE_KEY on Context ---


class ExplicitScopeApi(Protocol):
    @op.get("/scope-direct", guard=public)
    async def get_direct(self, c: Context[None], req: None) -> Ok[str]: ...


class ExplicitScopeService(ExplicitScopeApi):
    async def get_direct(self, c: Context[None], req: None) -> Ok[str]:
        scope = SCOPE_KEY.get(c)
        config = scope.get(AppConfig)
        return Ok(config.env)


@pytest.mark.asyncio
async def test_explicit_scope_key_access() -> None:
    container = Container()
    container.add_singleton(AppConfig, AppConfig(env="explicit-test"))

    app = App()
    app.use(DIMiddleware(container))
    container.wire(app, [ExplicitScopeService])

    async with TestClient(app) as client:
        resp = await client.get("/scope-direct")
        assert resp.status_code == 200
        assert resp.json() == "explicit-test"


# --- Test 6: Transient dependencies are created freshly on each access ---


class TransientCounter:
    _counter = 0

    def __init__(self) -> None:
        TransientCounter._counter += 1
        self.count = TransientCounter._counter


class TransientApi(Protocol):
    @op.get("/transient", guard=public)
    async def get_transient(self, c: Context[None], req: None) -> Ok[list[int]]: ...


class TransientService(TransientApi):
    t1: Injected[TransientCounter]
    t2: Injected[TransientCounter]

    async def get_transient(self, c: Context[None], req: None) -> Ok[list[int]]:
        return Ok([self.t1.count, self.t2.count])


@pytest.mark.asyncio
async def test_transient_lifecycle_injected_fresh() -> None:
    TransientCounter._counter = 0
    container = Container()
    container.add_transient(TransientCounter)

    app = App()
    app.use(DIMiddleware(container))
    container.wire(app, [TransientService])

    async with TestClient(app) as client:
        resp = await client.get("/transient")
        assert resp.status_code == 200
        # Accessing t1 and t2 should produce distinct instances with incremented counts
        assert resp.json() == [1, 2]


# --- Test 7: Explicit Injected(Type) without relying on annotations ---


def test_explicit_type_in_injected_constructor() -> None:
    container = Container()
    container.add_singleton(AppConfig, AppConfig(env="explicit-ctor"))
    provider = container.build_provider()

    class StandaloneService:
        config = Injected(AppConfig)

    # Class property access returns descriptor
    assert StandaloneService.config.service_type is AppConfig
    service = StandaloneService()

    with provider.create_scope() as scope:
        token = current_scope.set(scope)
        try:
            assert service.config.env == "explicit-ctor"
        finally:
            current_scope.reset(token)


# --- Test 8: Unannotated Injected descriptor without type raises TypeError ---


def test_unannotated_injected_descriptor_raises_type_error() -> None:
    container = Container()
    provider = container.build_provider()

    class BadService:
        untyped = Injected()

    service = BadService()
    with provider.create_scope() as scope:
        token = current_scope.set(scope)
        try:
            with pytest.raises(TypeError, match="no resolved service type"):
                _ = service.untyped
        finally:
            current_scope.reset(token)


# --- Test 9: Missing dependency raises error during startup wire() ---


class NeedsMissingDep:
    def __init__(self, nonexistent: bytes) -> None:
        self.nonexistent = nonexistent


class NeedsMissingApi(Protocol):
    @op.get("/missing", guard=public)
    async def missing(self, c: Context[None], req: None) -> NoContent: ...


class NeedsMissingService(NeedsMissingApi):
    def __init__(self, dep: NeedsMissingDep) -> None:
        self.dep = dep

    async def missing(self, c: Context[None], req: None) -> NoContent:
        return NoContent()


def test_wire_missing_dependency_raises_startup_error() -> None:
    container = Container()
    app = App()
    with pytest.raises(rodi.DIException):
        container.wire(app, [NeedsMissingService])
