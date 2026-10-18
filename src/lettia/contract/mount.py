"""Mount an implementation of one or more contracts onto a Core route target.

``mount`` reads the contract once, here. Everything it learns is compiled into
closures that Core registers as ordinary routes, so the contract layer takes no
part in request handling beyond running those closures.
"""

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from lettia.contract.codec import Codec, MsgspecCodec
from lettia.contract.context import Context as PrincipalContext
from lettia.contract.decode import compile_request
from lettia.contract.encode import compile_responses
from lettia.contract.guard_runtime import compile_guard, missing_implementations
from lettia.contract.guards import GuardImpl, GuardSpec
from lettia.contract.introspect import Operation, inspect_contract
from lettia.contract.op import operation_spec
from lettia.contract.responses import Reply
from lettia.core import Context, Handler, Response

_OPERATION_IDS_KEY = "lettia.operation_ids"
MOUNTED_KEY = "lettia.mounted_operations"
"""``App.state`` key of the list of :class:`MountedOperation`."""
GUARDS_KEY = "lettia.guards"
"""``App.state`` key under which the guard implementations are stored."""

type GuardRegistry = Mapping[GuardSpec[Any], GuardImpl[Any]]


@dataclass(frozen=True, slots=True)
class MountedOperation:
    """An operation together with the full path it is served under."""

    operation: Operation
    path: str


class MountTarget(Protocol):
    """What ``mount`` needs from an ``App`` or a ``Group``."""

    @property
    def prefix(self) -> str: ...

    @property
    def debug(self) -> bool: ...

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
    mounted = cast(list[MountedOperation], target.state.setdefault(MOUNTED_KEY, []))
    guards = cast(GuardRegistry, target.state.get(GUARDS_KEY, {}))
    for contract in contracts:
        operations, problems = inspect_contract(contract)
        for problem in problems:
            target.add_startup_problem(problem)
        for operation in operations:
            problems = _check(operation, implementation, operation_ids, guards)
            if problems:
                for problem in problems:
                    target.add_startup_problem(problem)
                continue
            handler = _compile(
                operation, implementation, codec, guards, debug=target.debug
            )
            target.route(operation.spec.method, operation.spec.path, handler)
            mounted.append(
                MountedOperation(operation, target.prefix + operation.spec.path)
            )


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
    operation: Operation,
    implementation: object,
    operation_ids: dict[str, str],
    guards: GuardRegistry,
) -> list[str]:
    """Startup checks 4, 5 and 6 for one operation."""
    implementation_type = type(implementation)
    contract_name = f"{operation.contract.__qualname__}.{operation.name}"
    problems: list[str] = []

    for missing in missing_implementations(operation.spec.guard, guards):
        problems.append(
            f"{contract_name}: guard {missing.name!r} has no implementation; "
            "bind one with guard.use(fn) and pass it to App(guards=[...])"
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


def _compile(
    operation: Operation,
    implementation: object,
    codec: Codec,
    guards: GuardRegistry,
    *,
    debug: bool,
) -> Handler:
    """Chain guard, decode, call and encode into one Core handler."""
    run_guard = compile_guard(operation.spec.guard, guards)
    decode = compile_request(operation, codec)
    encode = compile_responses(operation, codec, debug=debug)
    call = cast(
        Callable[[Context, Any], Awaitable[Reply]],
        getattr(implementation, operation.name),
    )

    async def handle(c: Context) -> Response:
        if run_guard is not None:
            # Guards read only the head, so they run before anything is decoded.
            cast(PrincipalContext[Any], c).principal = await run_guard(c.request)
        request = None if decode is None else await decode(c)
        return encode(await call(c, request))

    return handle
