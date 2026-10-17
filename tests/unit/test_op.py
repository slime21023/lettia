import dataclasses
from typing import Protocol

import pytest

import lettia
from lettia import Context, GuardSpec, NoContent, op, public
from lettia.contract.op import OperationSpec, operation_spec


class Admin: ...


admin = GuardSpec[Admin]("admin")


class Api(Protocol):
    @op.get("/things/{thing_id}", guard=public, tags=["things", "read"])
    async def get_thing(self, c: Context[None], req: None) -> NoContent:
        """Get one thing."""
        ...

    @op.post("/things", guard=admin, operation_id="createThing")
    async def create_thing(self, c: Context[Admin], req: None) -> NoContent: ...


def test_op_returns_the_decorated_function_unchanged() -> None:
    async def handler(self: object, c: Context[None], req: None) -> NoContent:
        return NoContent()

    assert op.get("/x", guard=public)(handler) is handler


def test_op_records_method_path_guard_and_defaults() -> None:
    spec = operation_spec(Api.create_thing)

    assert spec == OperationSpec(
        method="POST",
        path="/things",
        guard=admin,
        tags=(),
        operation_id="createThing",
        openapi_extra=None,
    )
    assert spec is not None
    assert spec.guard is admin


def test_op_records_tags_as_a_tuple() -> None:
    spec = operation_spec(Api.get_thing)

    assert spec is not None
    assert spec.tags == ("things", "read")
    assert spec.operation_id is None


@pytest.mark.parametrize("verb", ["get", "post", "put", "patch", "delete"])
def test_op_supports_every_http_verb(verb: str) -> None:
    async def handler(self: object, c: Context[None], req: None) -> NoContent:
        return NoContent()

    decorated = getattr(op, verb)("/x", guard=public)(handler)

    spec = operation_spec(decorated)
    assert spec is not None
    assert spec.method == verb.upper()


def test_op_keeps_openapi_extra() -> None:
    @op.get("/x", guard=public, openapi_extra={"x-internal": True})
    async def handler(self: object, c: Context[None], req: None) -> NoContent:
        return NoContent()

    spec = operation_spec(handler)
    assert spec is not None
    assert spec.openapi_extra == {"x-internal": True}


def test_operation_spec_of_an_undecorated_function_is_none() -> None:
    async def plain() -> None:
        return None

    assert operation_spec(plain) is None


def test_operation_spec_is_immutable() -> None:
    spec = operation_spec(Api.get_thing)

    assert spec is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.path = "/other"  # type: ignore[misc]


@pytest.mark.parametrize("path", ["things", "", "{id}"])
def test_op_rejects_a_path_that_does_not_start_with_a_slash(path: str) -> None:
    with pytest.raises(ValueError, match="path"):
        op.get(path, guard=public)


def test_op_rejects_a_guard_that_is_not_a_guard_spec() -> None:
    with pytest.raises(TypeError, match="guard"):
        op.get("/x", guard="bearer")  # type: ignore[call-overload]


def test_op_is_exported_from_the_top_level_package() -> None:
    assert lettia.op is op
