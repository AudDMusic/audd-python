"""Retry attempt counts, upload filenames, and sources that can't be resent."""
from __future__ import annotations

import io
from typing import Any

import httpx
import pytest
import respx

from audd import AsyncAudD, AudD, AudDConnectionError, AudDServerError
from audd._retry import RetryClass, RetryPolicy, retry_sync
from audd._source import prepare_source


@pytest.mark.parametrize("max_retries", [0, -1, 1])
@respx.mock
def test_max_retries_below_two_sends_exactly_one_attempt(max_retries: int) -> None:
    route = respx.post("https://api.audd.io/").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(AudDConnectionError):
        AudD(api_token="t", max_retries=max_retries).recognize("https://example.mp3")
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_async_max_retries_zero_sends_one_attempt() -> None:
    route = respx.post("https://api.audd.io/").mock(side_effect=httpx.ConnectError("down"))
    async with AsyncAudD(api_token="t", max_retries=0) as audd:
        with pytest.raises(AudDConnectionError):
            await audd.recognize("https://example.mp3")
    assert route.call_count == 1


def test_retry_policy_with_zero_attempts_returns_the_response() -> None:
    calls: list[int] = []

    def fn() -> Any:
        from audd._http import HTTPResponse
        calls.append(1)
        return HTTPResponse(json_body={}, http_status=200, request_id=None, raw_text="{}")

    retry_sync(fn, RetryPolicy(RetryClass.READ, max_attempts=0))
    assert calls == [1]


class _NamedStream(io.RawIOBase):
    """A readable stream with a configurable ``name`` (like an opened file)."""

    def __init__(self, data: bytes, name: Any) -> None:
        self._buf = io.BytesIO(data)
        self.name = name

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        chunk = self._buf.read(len(b))
        b[: len(chunk)] = chunk
        return len(chunk)


@pytest.mark.parametrize(("name", "expected"), [
    ("/home/someone/private-dir/clip.mp3", "clip.mp3"),
    (b"/tmp/x/song.wav", "song.wav"),
    (7, "upload.bin"),
    ("", "upload.bin"),
    ("/trailing/slash/", "upload.bin"),
])
def test_file_like_upload_sends_only_the_base_name(name: Any, expected: str) -> None:
    _data, files, _cleanup = prepare_source(_NamedStream(b"\x00", name))()
    assert files is not None
    assert files["file"][0] == expected


@respx.mock
def test_recognize_upload_filename_has_no_directory(tmp_path: Any) -> None:
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.read())
        return httpx.Response(200, json={"status": "success", "result": None})

    respx.post("https://api.audd.io/").mock(side_effect=handler)
    path = tmp_path / "private-dir" / "clip.mp3"
    path.parent.mkdir()
    path.write_bytes(b"\x00" * 8)
    with path.open("rb") as fh:
        AudD(api_token="t").recognize(fh)
    assert b'filename="clip.mp3"' in seen[0]
    assert str(tmp_path).encode() not in seen[0]


class _Unseekable(io.RawIOBase):
    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        chunk = self._buf.read(len(b))
        b[: len(chunk)] = chunk
        return len(chunk)

    def tell(self) -> int:
        raise OSError("not seekable")


@respx.mock
def test_unseekable_source_after_connect_error_raises_the_connection_error() -> None:
    route = respx.post("https://api.audd.io/").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(AudDConnectionError, match="refused"):
        AudD(api_token="t", backoff_factor=0).recognize(_Unseekable(b"abc"))
    assert route.call_count == 1


@respx.mock
def test_unseekable_source_after_5xx_returns_the_server_error() -> None:
    route = respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(502, text="bad gateway"),
    )
    with pytest.raises(AudDServerError) as ei:
        AudD(api_token="t", backoff_factor=0).recognize(_Unseekable(b"abc"))
    assert ei.value.http_status == 502
    assert route.call_count == 1
