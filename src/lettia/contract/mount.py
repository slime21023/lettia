"""Mount an implementation of one or more contracts onto a Core route target.

``mount`` reads the contract once, here. Everything it learns is compiled into
closures that Core registers as ordinary routes, so the contract layer takes no
part in request handling beyond running those closures.
"""

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast

from lettia.contract.codec import Codec, MsgspecCodec
from lettia.contract.decode import compile_request
from lettia.contract.encode import compile_responses
from lettia.contract.guards import public
from lettia.contract.introspect import Operation, inspect_contract
from lettia.contract.op import operation_spec
from lettia.contract.responses import Reply
from lettia.core import Context, Handler, Response

_OPERATION_IDS_KEY = "lettia.operation_ids"


class MountTarget(Protocol):
    """What ``mount`` needs from an ``App`` or a ``Group``."""

    @property
    def state(self) -> dict[str, object]: ...

    def route(self, method: str, path: str, handler: Handler) -> None: ...

    def add_startup_problem(self, message: str) -> None: ...


def mount(
    target: MountTarget, implementation: object, *, codec: Codec | None = None
) -> None:
    """Register every operation of the contracts ``implementation`` inherits.

    Problems do not raise here: they are recorded on ``target`` so that startup
    reports them together with everything else that is wrong.
    """
    codec = codec or MsgspecCodec()
    implementation_type = type(implementation)
    contracts = _contracts_of(implementation_type)
    if not contracts:
        target.add_startup_problem(
            f"{implementation_type.__qualname__} does not implement any contract "
            "(no class in its MRO declares op methods)"
        )
        return

    operation_ids = _operation_ids(target)
    for contract in contracts:
        operations, problems = inspect_contract(contract)
        for problem in problems:
            target.add_startup_problem(problem)
        for operation in operations:
            problems = _check(operation, implementation, operation_ids)
            if problems:
                for problem in problems:
                    target.add_startup_problem(problem)
                continue
            handler = _compile(operation, implementation, codec)
            target.route(operation.spec.method, operation.spec.path, handler)


def _contracts_of(cls: type) -> list[type]:
    """Classes in ``cls``'s MRO that declare ``op`` methods, most derived first."""
    return [
        base
        for base in cls.__mro__
        if any(operation_spec(member) is not None for member in vars(base).values())
    ]


def _operation_ids(target: MountTarget) -> dict[str, str]:
    return cast(dict[str, str], target.state.setdefault(_OPERATION_IDS_KEY, {}))


def _check(
    operation: Operation, implementation: object, operation_ids: dict[str, str]
) -> list[str]:
    """Startup checks 4 (for now only ``public``), 5 and 6 for one operation."""
    implementation_type = type(implementation)
    contract_name = f"{operation.contract.__qualname__}.{operation.name}"
    problems: list[str] = []

    guard = operation.spec.guard
    if guard is not public:
        problems.append(
            f"{contract_name}: guard {guard.name!r} cannot be used yet; "
            "only the public guard is supported so far"
        )

    owner = next(c for c in implementation_type.__mro__ if operation.name in vars(c))
    if operation_spec(vars(owner)[operation.name]) is not None:
        problems.append(
            f"{implementation_type.__qualname__} does not implement {contract_name}"
        )
    elif not inspect.iscoroutinefunction(getattr(implementation, operation.name)):
        problems.append(
            f"{implementation_type.__qualname__}.{operation.name} must be an "
            "async function"
        )

    earlier = operation_ids.get(operation.operation_id)
    if earlier is not None:
        problems.append(
            f"operationId {operation.operation_id!r} of {contract_name} "
            f"is already used by {earlier}"
        )
    else:
        operation_ids[operation.operation_id] = contract_name
    return problems


def _compile(operation: Operation, implementation: object, codec: Codec) -> Handler:
    decode = compile_request(operation, codec)
    encode = compile_responses(operation, codec)
    call = cast(
        Callable[[Context, Any], Awaitable[Reply]],
        getattr(implementation, operation.name),
    )

    if decode is None:

        async def handle_without_request(c: Context) -> Response:
            return encode(await call(c, None))

        return handle_without_request

    async def handle(c: Context) -> Response:
        return encode(await call(c, await decode(c)))

    return handle
