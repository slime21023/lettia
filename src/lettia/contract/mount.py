"""Mount an implementation of one or more contracts onto a Core route target.

``mount`` reads the contract once, here. Everything it learns is compiled into
closures that Core registers as ordinary routes, so the contract layer takes no
part in request handling beyond running those closures.
"""

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
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

_CONTRACTS_KEY = "lettia.contracts"

type GuardRegistry = Mapping[GuardSpec[Any], GuardImpl[Any]]


@dataclass(frozen=True, slots=True)
class MountedOperation:
    """An operation together with the full path it is served under."""

    operation: Operation
    path: str


class ContractRegistry:
    """Contract-layer state shared across an ``App`` and its route groups."""

    __slots__ = ("codec", "guards", "mounted", "operation_ids")

    def __init__(self) -> None:
        self.codec: Codec = MsgspecCodec()
        self.guards: dict[GuardSpec[Any], GuardImpl[Any]] = {}
        self.mounted: list[MountedOperation] = []
        self.operation_ids: dict[str, str] = {}

    def register_guards(
        self, target: "MountTarget", guards: Sequence[GuardImpl[Any]]
    ) -> None:
        """Validate and record guard implementations onto this registry."""
        for implementation in guards:
            if not isinstance(cast(object, implementation), GuardImpl):
                raise TypeError(
                    "guards must be made with spec.use(fn), "
                    f"got {type(implementation).__name__}"
                )
            if implementation.spec in self.guards:
                target.add_startup_problem(
                    f"guard {implementation.spec.name!r} is implemented more than once"
                )
                continue
            self.guards[implementation.spec] = implementation


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


def contract_registry(target: MountTarget) -> ContractRegistry:
    """Return (or create) the :class:`ContractRegistry` stored on ``target``."""
    registry = target.state.get(_CONTRACTS_KEY)
    if not isinstance(registry, ContractRegistry):
        registry = ContractRegistry()
        target.state[_CONTRACTS_KEY] = registry
    return registry


def mount(
    target: MountTarget, implementation: object, *, codec: Codec | None = None
) -> None:
    """Register every operation of the contracts ``implementation`` inherits.

    Problems do not raise here: they are recorded on ``target`` so that startup
    reports them together with everything else that is wrong.
    """
    registry = contract_registry(target)
    active_codec = codec or registry.codec
    implementation_type = type(implementation)
    contracts = _contracts_of(implementation_type)
    if not contracts:
        target.add_startup_problem(
            f"{implementation_type.__qualname__} does not implement any contract "
            "(no class in its MRO declares op methods)"
        )
        return

    for contract in contracts:
        operations, problems = inspect_contract(contract)
        for problem in problems:
            target.add_startup_problem(problem)
        for operation in operations:
            problems = _check(
                operation,
                implementation,
                registry.operation_ids,
                registry.guards,
            )
            if problems:
                for problem in problems:
                    target.add_startup_problem(problem)
                continue
            handler = _compile(
                operation,
                implementation,
                active_codec,
                registry.guards,
                debug=target.debug,
            )
            target.route(operation.spec.method, operation.spec.path, handler)
            registry.mounted.append(
                MountedOperation(operation, target.prefix + operation.spec.path)
            )


def _contracts_of(cls: type) -> list[type]:
    """Classes in ``cls``'s MRO that declare ``op`` methods, most derived first."""
    return [
        base
        for base in cls.__mro__
        if any(operation_spec(member) is not None for member in vars(base).values())
    ]


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
