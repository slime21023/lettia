"""Static files serving extension for lettia."""

from lettia.extensions.staticfiles.files import StaticFiles
from lettia.extensions.staticfiles.range import ByteRange, parse_range_header

__all__ = [
    "ByteRange",
    "StaticFiles",
    "parse_range_header",
]
