import pytest

from lettia.contract.form import UploadFile, parse_form_data
from lettia.core import HTTPError, Request


async def stream_of(*chunks: bytes):
    for c in chunks:
        yield c


def make_request(
    content_type: str, *body_chunks: bytes, max_body_size: int | None = None
) -> Request:
    return Request(
        method="POST",
        path="/upload",
        headers=[(b"content-type", content_type.encode("latin-1"))],
        body=stream_of(*body_chunks),
        max_body_size=max_body_size,
    )


# --- UploadFile unit tests ----------------------------------------------


async def test_upload_file_in_memory_read_seek_stream() -> None:
    upload = UploadFile(filename="test.txt", content_type="text/plain")
    assert upload.filename == "test.txt"
    assert upload.content_type == "text/plain"
    assert upload.size == 0
    assert repr(upload) == (
        "UploadFile(filename='test.txt', content_type='text/plain', size=0)"
    )

    # Write data
    upload.write(b"hello world")
    assert upload.size == 11
    assert upload.rolled_to_disk is False

    await upload.seek(0)
    assert await upload.read(5) == b"hello"
    assert await upload.read() == b" world"

    # Stream
    chunks = [c async for c in upload.stream(chunk_size=4)]
    assert b"".join(chunks) == b"hello world"

    await upload.close()


async def test_upload_file_spools_to_disk_when_large() -> None:
    upload = UploadFile(filename="large.bin")
    large_data = b"x" * (1024 * 1024 + 100)  # > 1 MiB
    upload.write(large_data)

    assert upload.size == len(large_data)
    assert upload.rolled_to_disk is True

    await upload.seek(0)
    assert len(await upload.read()) == len(large_data)
    await upload.close()


# --- URL-encoded form parsing -------------------------------------------


async def test_parse_urlencoded_form() -> None:
    req = make_request(
        "application/x-www-form-urlencoded",
        b"name=Alice&age=30&tag=admin&tag=staff&city=%E5%8F%B0%E5%8C%97",
    )
    form_data, files = await parse_form_data(req)

    assert files == []
    assert form_data["name"] == ["Alice"]
    assert form_data["age"] == ["30"]
    assert form_data["tag"] == ["admin", "staff"]
    assert form_data["city"] == ["台北"]


# --- Multipart form parsing --------------------------------------------


async def test_parse_multipart_simple_text_fields() -> None:
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    payload = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="title"\r\n\r\n'
        "Hello World\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="author"\r\n\r\n'
        "Kevin\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    req = make_request(f"multipart/form-data; boundary={boundary}", payload)
    form_data, files = await parse_form_data(req)

    assert files == []
    assert form_data["title"] == ["Hello World"]
    assert form_data["author"] == ["Kevin"]


async def test_parse_multipart_single_file() -> None:
    boundary = "----Boundary123"
    file_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    payload = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="avatar"; filename="avatar.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode("latin-1")
        + file_bytes
        + f"\r\n--{boundary}--\r\n".encode("latin-1")
    )

    req = make_request(f"multipart/form-data; boundary={boundary}", payload)
    form_data, files = await parse_form_data(req)

    assert form_data == {}
    assert len(files) == 1
    upload = files[0]
    assert upload.name == "avatar"
    assert upload.filename == "avatar.png"
    assert upload.content_type == "image/png"
    assert upload.size == len(file_bytes)
    assert await upload.read() == file_bytes
    await upload.close()


async def test_parse_multipart_mixed_form_and_files() -> None:
    boundary = "----Boundary456"
    file_bytes = b"sample text content"
    payload = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="username"\r\n\r\n'
            "alice\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="resume"; filename="resume.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode()
        + file_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )

    req = make_request(f"multipart/form-data; boundary={boundary}", payload)
    form_data, files = await parse_form_data(req)

    assert form_data["username"] == ["alice"]
    assert len(files) == 1
    assert files[0].name == "resume"
    assert files[0].filename == "resume.txt"
    assert await files[0].read() == file_bytes
    await files[0].close()


async def test_parse_multipart_multiple_files_same_name() -> None:
    boundary = "----BoundaryMulti"
    payload = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="docs"; filename="doc1.txt"\r\n\r\n'
        "first file\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="docs"; filename="doc2.txt"\r\n\r\n'
        "second file\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    req = make_request(f"multipart/form-data; boundary={boundary}", payload)
    form_data, files = await parse_form_data(req)

    assert len(files) == 2
    assert files[0].name == "docs"
    assert files[0].filename == "doc1.txt"
    assert await files[0].read() == b"first file"
    assert files[1].name == "docs"
    assert files[1].filename == "doc2.txt"
    assert await files[1].read() == b"second file"
    for f in files:
        await f.close()


async def test_parse_multipart_across_fragmented_chunks() -> None:
    boundary = "----FragmentBoundary"
    payload = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="memo"\r\n\r\n'
        "streaming test memo\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="attachment"; '
        'filename="attach.dat"\r\n\r\n'
        "binary chunk data\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    # Slice into tiny 3-byte chunks to test fragmented boundary splitting
    chunks = [payload[i : i + 3] for i in range(0, len(payload), 3)]
    req = make_request(f"multipart/form-data; boundary={boundary}", *chunks)
    form_data, files = await parse_form_data(req)

    assert form_data["memo"] == ["streaming test memo"]
    assert len(files) == 1
    assert files[0].name == "attachment"
    assert files[0].filename == "attach.dat"
    assert await files[0].read() == b"binary chunk data"
    await files[0].close()


async def test_parse_multipart_utf8_filename() -> None:
    boundary = "----BoundaryUtf8"
    payload = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="doc"; filename="測試檔案.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
        "pdf-bytes\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    req = make_request(f"multipart/form-data; boundary={boundary}", payload)
    _, files = await parse_form_data(req)

    assert len(files) == 1
    assert files[0].filename == "測試檔案.pdf"
    await files[0].close()


async def test_parse_multipart_missing_boundary_raises_400() -> None:
    req = make_request("multipart/form-data", b"something")
    with pytest.raises(HTTPError) as caught:
        await parse_form_data(req)
    assert caught.value.status == 400
    assert "boundary" in (caught.value.detail or "").lower()


async def test_parse_multipart_unexpected_eof_raises_400() -> None:
    boundary = "----BoundaryTruncated"
    payload = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="field"\r\n\r\n'
        "truncated content without closing boundary"
    ).encode()

    req = make_request(f"multipart/form-data; boundary={boundary}", payload)
    with pytest.raises(HTTPError) as caught:
        await parse_form_data(req)
    assert caught.value.status == 400
    detail = (caught.value.detail or "").lower()
    assert "eof" in detail or "unexpected" in detail
