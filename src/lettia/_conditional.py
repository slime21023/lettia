import re

_ENTITY_TAG = re.compile(r'(?:W/)?"[\x21\x23-\x7e\x80-\xff]*"')


def select_byte_range(
    method: str,
    range_header: str | None,
    if_range: str | None,
    etag: str,
    file_size: int,
) -> tuple[int, int] | None:
    if (
        method == "GET"
        and range_header
        and range_header.startswith("bytes=")
        and (if_range is None or if_range.strip(" \t") == etag)
    ):
        return byte_range(range_header, file_size)
    return None


def if_none_match(value: str, etag: str) -> bool:
    value = value.strip(" \t")
    if value == "*":
        return True
    matched = False
    position = 0
    # Empty list members are allowed by HTTP list syntax; commas inside tags
    # belong to the opaque tag and must not split a member.
    while position < len(value):
        if value[position] in " \t,":
            position += 1
            continue
        tag = _ENTITY_TAG.match(value, position)
        if tag is None:
            return False
        matched |= tag[0].removeprefix("W/") == etag
        position = tag.end()
        while position < len(value) and value[position] in " \t":
            position += 1
        if position < len(value) and value[position] != ",":
            return False
    return matched


def byte_range(range_header: str, file_size: int) -> tuple[int, int]:
    bytes_range = range_header.removeprefix("bytes=").strip()
    if "," in bytes_range:
        raise ValueError("Range Not Satisfiable")
    start_str, end_str = bytes_range.split("-", 1)
    if not start_str and not end_str:
        raise ValueError("Range Not Satisfiable")

    if not start_str:
        suffix_length = int(end_str)
        if suffix_length <= 0:
            raise ValueError("Range Not Satisfiable")
        start = max(file_size - suffix_length, 0)
        end = file_size - 1
    else:
        start = int(start_str)
        end = int(end_str) if end_str else file_size - 1
        if start >= file_size:
            raise ValueError("Range Not Satisfiable")
        end = min(end, file_size - 1)

    if start > end:
        raise ValueError("Range Not Satisfiable")

    return start, end
