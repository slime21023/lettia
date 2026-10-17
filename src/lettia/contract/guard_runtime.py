"""What guards do at request time.

``public`` is not run at all. ``a | b`` returns the first branch that succeeds;
``a & b`` runs both and returns ``(principal_a, principal_b)``. Only
``Unauthorized`` and ``Forbidden`` mean "this guard said no": any other failure
is a bug or an outage and propagates, so a crashing verifier never lets a request
fall through to the next branch.
"""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from lettia.contract.guards import GuardImpl, GuardSpec, public
from lettia.core import Forbidden, RequestHead, Unauthorized

type GuardRunner = Callable[[RequestHead], Awaitable[Any]]


def missing_implementations(
    spec: GuardSpec[Any], implementations: Mapping[GuardSpec[Any], GuardImpl[Any]]
) -> list[GuardSpec[Any]]:
    """Startup check 4: the named guards of ``spec`` that nobody implemented."""
    return [
        leaf
        for leaf in spec.leaves()
        if leaf is not public and leaf not in implementations
    ]


def compile_guard(
    spec: GuardSpec[Any], implementations: Mapping[GuardSpec[Any], GuardImpl[Any]]
) -> GuardRunner | None:
    """A function that returns the principal or raises, or ``None`` for ``public``.

    Every named guard in ``spec`` must have an implementation (see
    :func:`missing_implementations`).
    """
    if spec is public:
        return None
    return _compile(spec, implementations)


async def _anonymous(head: RequestHead) -> None:
    return None


def _compile(
    spec: GuardSpec[Any], implementations: Mapping[GuardSpec[Any], GuardImpl[Any]]
) -> GuardRunner:
    if spec.kind == "leaf":
        if spec is public:
            return _anonymous
        return implementations[spec].fn

    runners = [_compile(part, implementations) for part in spec.parts]
    if spec.kind == "and":
        first, second = runners

        async def run_all(head: RequestHead) -> Any:
            return (await first(head), await second(head))

        return run_all

    async def run_any(head: RequestHead) -> Any:
        refusals: list[Unauthorized | Forbidden] = []
        for runner in runners:
            try:
                return await runner(head)
            except (Unauthorized, Forbidden) as refusal:
                refusals.append(refusal)
        forbidden = next((r for r in refusals if isinstance(r, Forbidden)), None)
        raise forbidden or refusals[0]

    return run_any
