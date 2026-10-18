from typing import Any, cast

import pytest

from lettia import GuardSpec, RequestHead, public
from lettia.contract.guard_runtime import compile_guard, missing_implementations
from lettia.contract.guards import GuardImpl
from lettia.contract.mount import ContractRegistry
from lettia.core import Forbidden, HTTPError, Unauthorized

bearer = GuardSpec[str]("bearer")
apikey = GuardSpec[int]("apikey")
admin = GuardSpec[bool]("admin")

HEAD = RequestHead(method="GET", path="/")


class Calls:
    """Records the order in which guard implementations ran."""

    def __init__(self) -> None:
        self.log: list[str] = []


def ok[T](calls: Calls, name: str, value: T) -> Any:
    async def impl(head: RequestHead) -> T:
        calls.log.append(name)
        return value

    return impl


def fail(calls: Calls, name: str, error: Exception) -> Any:
    async def impl(head: RequestHead) -> Any:
        calls.log.append(name)
        raise error

    return impl


def implementations(
    *pairs: tuple[GuardSpec[Any], Any],
) -> dict[GuardSpec[Any], GuardImpl[Any]]:
    return {spec: spec.use(fn) for spec, fn in pairs}


# --- the spec tree ------------------------------------------------------


def test_a_plain_guard_is_a_leaf() -> None:
    assert bearer.kind == "leaf"
    assert bearer.parts == ()
    assert bearer.leaves() == (bearer,)


def test_or_and_and_build_composite_nodes() -> None:
    either = bearer | apikey
    both = bearer & admin

    assert (either.kind, either.parts) == ("or", (bearer, apikey))
    assert (both.kind, both.parts) == ("and", (bearer, admin))


def test_chained_or_is_flattened() -> None:
    third = GuardSpec[str]("third")

    chained = bearer | apikey | third

    assert chained.kind == "or"
    assert chained.parts == (bearer, apikey, third)


def test_chained_and_stays_nested_like_its_type() -> None:
    chained = bearer & apikey & admin

    assert chained.kind == "and"
    left, right = chained.parts
    assert left.kind == "and"
    assert left.parts == (bearer, apikey)
    assert right is admin


def test_leaves_lists_each_guard_once_in_order() -> None:
    spec = (bearer & admin) | (bearer & apikey)

    assert spec.leaves() == (bearer, admin, apikey)


def test_leaves_include_public_inside_a_composite() -> None:
    assert (bearer | public).leaves() == (bearer, public)


def test_a_guard_is_implemented_with_use() -> None:
    async def verify(head: RequestHead) -> str:
        return "ann"

    implementation = bearer.use(verify)

    assert implementation.spec is bearer
    assert implementation.fn is verify


def test_only_a_single_named_guard_can_be_implemented() -> None:
    async def verify(head: RequestHead) -> Any:
        return None

    with pytest.raises(TypeError, match="composite"):
        (bearer | apikey).use(verify)
    with pytest.raises(TypeError, match="public"):
        public.use(verify)


def test_composite_names_show_their_parts() -> None:
    assert (bearer | apikey).name == "(bearer | apikey)"
    assert (bearer & admin).name == "(bearer & admin)"


# --- running guards -----------------------------------------------------


async def test_public_needs_no_runner() -> None:
    assert compile_guard(public, {}) is None


async def test_a_leaf_returns_what_its_implementation_returns() -> None:
    calls = Calls()
    run = compile_guard(bearer, implementations((bearer, ok(calls, "b", "ann"))))

    assert run is not None
    assert await run(HEAD) == "ann"


async def test_a_leaf_failure_propagates_unchanged() -> None:
    error = Unauthorized("bad token", headers={"www-authenticate": "Bearer"})
    run = compile_guard(bearer, implementations((bearer, fail(Calls(), "b", error))))

    assert run is not None
    with pytest.raises(Unauthorized) as caught:
        await run(HEAD)

    assert caught.value is error


async def test_or_returns_the_first_success_and_skips_the_rest() -> None:
    calls = Calls()
    impls = implementations(
        (bearer, fail(calls, "bearer", Unauthorized())),
        (apikey, ok(calls, "apikey", 42)),
        (admin, ok(calls, "admin", True)),
    )
    run = compile_guard(bearer | apikey | admin, impls)

    assert run is not None
    assert await run(HEAD) == 42
    assert calls.log == ["bearer", "apikey"]


async def test_or_with_every_branch_unauthorized_raises_the_first_unauthorized() -> (
    None
):
    first, second = Unauthorized("first"), Unauthorized("second")
    calls = Calls()
    impls = implementations(
        (bearer, fail(calls, "bearer", first)), (apikey, fail(calls, "apikey", second))
    )
    run = compile_guard(bearer | apikey, impls)

    assert run is not None
    with pytest.raises(Unauthorized) as caught:
        await run(HEAD)

    assert caught.value is first
    assert calls.log == ["bearer", "apikey"]


async def test_or_raises_forbidden_when_any_branch_was_forbidden() -> None:
    forbidden = Forbidden("no")
    impls = implementations(
        (bearer, fail(Calls(), "b", Unauthorized())),
        (apikey, fail(Calls(), "a", forbidden)),
    )
    run = compile_guard(bearer | apikey, impls)

    assert run is not None
    with pytest.raises(Forbidden) as caught:
        await run(HEAD)

    assert caught.value is forbidden


@pytest.mark.parametrize("crash", [RuntimeError("bug"), HTTPError(503, "Unavailable")])
async def test_or_does_not_treat_a_crash_as_a_failed_branch(crash: Exception) -> None:
    calls = Calls()
    impls = implementations(
        (bearer, fail(calls, "bearer", crash)), (apikey, ok(calls, "apikey", 1))
    )
    run = compile_guard(bearer | apikey, impls)

    assert run is not None
    with pytest.raises(type(crash)):
        await run(HEAD)

    assert calls.log == ["bearer"]


async def test_or_with_public_gives_an_optional_principal() -> None:
    impls = implementations((bearer, fail(Calls(), "b", Unauthorized())))
    run = compile_guard(bearer | public, impls)

    assert run is not None
    assert await run(HEAD) is None


async def test_and_returns_every_principal_in_order() -> None:
    calls = Calls()
    impls = implementations(
        (bearer, ok(calls, "bearer", "ann")), (admin, ok(calls, "admin", True))
    )
    run = compile_guard(bearer & admin, impls)

    assert run is not None
    assert await run(HEAD) == ("ann", True)
    assert calls.log == ["bearer", "admin"]


async def test_and_stops_at_the_first_failure() -> None:
    calls = Calls()
    impls = implementations(
        (bearer, fail(calls, "bearer", Unauthorized())),
        (admin, ok(calls, "admin", True)),
    )
    run = compile_guard(bearer & admin, impls)

    assert run is not None
    with pytest.raises(Unauthorized):
        await run(HEAD)

    assert calls.log == ["bearer"]


async def test_and_nests_principals_like_its_type() -> None:
    calls = Calls()
    impls = implementations(
        (bearer, ok(calls, "b", "ann")),
        (apikey, ok(calls, "k", 7)),
        (admin, ok(calls, "a", True)),
    )
    run = compile_guard(bearer & apikey & admin, impls)

    assert run is not None
    assert await run(HEAD) == (("ann", 7), True)


async def test_or_of_ands_tries_the_whole_conjunction_per_branch() -> None:
    calls = Calls()
    impls = implementations(
        (bearer, ok(calls, "bearer", "ann")),
        (admin, fail(calls, "admin", Forbidden("not admin"))),
        (apikey, ok(calls, "apikey", 9)),
    )
    run = compile_guard((bearer & admin) | apikey, impls)

    assert run is not None
    assert await run(HEAD) == 9
    assert calls.log == ["bearer", "admin", "apikey"]


# --- startup check 4 ----------------------------------------------------


def test_missing_implementations_lists_unimplemented_leaves_once() -> None:
    impls = implementations((bearer, ok(Calls(), "b", "x")))

    missing = missing_implementations((bearer & admin) | admin | apikey, impls)

    assert missing == [admin, apikey]


def test_missing_implementations_ignores_public() -> None:
    assert missing_implementations(public, {}) == []
    assert missing_implementations(bearer | public, {}) == [bearer]


def test_register_guards_rejects_object_not_created_with_use() -> None:
    class DummyTarget:
        def __init__(self) -> None:
            self.problems: list[str] = []

        def add_startup_problem(self, message: str) -> None:
            self.problems.append(message)

    registry = ContractRegistry()
    target: Any = DummyTarget()
    with pytest.raises(TypeError, match="guards must be made with spec.use"):
        registry.register_guards(target, [cast(Any, object())])


def test_register_guards_reports_duplicate_implementations() -> None:
    class DummyTarget:
        def __init__(self) -> None:
            self.problems: list[str] = []

        def add_startup_problem(self, message: str) -> None:
            self.problems.append(message)

    async def first(head: RequestHead) -> str:
        return "1"

    async def second(head: RequestHead) -> str:
        return "2"

    registry = ContractRegistry()
    target: Any = DummyTarget()
    registry.register_guards(target, [bearer.use(first), bearer.use(second)])

    assert len(target.problems) == 1
    assert "guard 'bearer' is implemented more than once" in target.problems[0]
