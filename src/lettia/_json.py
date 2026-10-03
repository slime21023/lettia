from typing import cast

from lettia.asgi import JSONValue


def validate_json_value(value: object) -> JSONValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        items = cast(list[object], value)
        return [validate_json_value(item) for item in items]
    if isinstance(value, dict):
        converted: dict[str, JSONValue] = {}
        items = cast(dict[object, object], value)
        for key, item in items.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            converted[key] = validate_json_value(item)
        return converted
    raise TypeError(f"Decoded JSON has unsupported type: {type(value).__name__}")
