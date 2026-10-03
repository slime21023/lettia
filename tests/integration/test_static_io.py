import asyncio
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from lettia.ext import StaticFiles
from lettia.ext.static import _file_stream
from tests.support.asgi import (
    http_context,
)


@pytest.mark.contract("STATIC-IO")
async def test_static_stream_cancellation_during_open_closes_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "file.txt"
    path.write_bytes(b"ok")
    opened = path.open("rb")
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def slow_open(*args: object, **kwargs: object):
        loop.call_soon_threadsafe(entered.set)
        release.wait(timeout=2)
        return opened

    stream = _file_stream(path)
    with patch.object(Path, "open", side_effect=slow_open):
        task = asyncio.create_task(anext(stream))
        try:
            async with asyncio.timeout(2):
                await entered.wait()
                task.cancel()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await stream.aclose()
    assert opened.closed


@pytest.mark.contract("STATIC-IO")
async def test_static_file_operations_are_offloaded() -> None:
    original = asyncio.to_thread
    calls: list[object] = []

    async def track[T](func: Callable[..., T], /, *args: object, **kwargs: object) -> T:
        calls.append(func)
        return await original(func, *args, **kwargs)

    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "file.txt").write_bytes(b"hello")
        with patch("lettia.ext.static.asyncio.to_thread", track):
            response = await StaticFiles(directory).handle(
                http_context(path="/file.txt")
            )
            assert response.async_body is not None
            assert b"".join([chunk async for chunk in response.async_body]) == b"hello"
        assert calls
