from attrs import define


@define(slots=True, frozen=True)
class Route[HandlerT]:
    method: str
    path: str
    handler: HandlerT
    name: str | None = None
