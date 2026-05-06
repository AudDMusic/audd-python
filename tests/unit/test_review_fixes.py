"""Regression tests for issues caught during the v0.1.0 code review.

Each test name maps to a finding ID (C1, C2, C3, S1, S2, S5, S6, M2).
"""
from __future__ import annotations

import io
import warnings
from pathlib import Path

import httpx
import pytest
import respx

from audd import AsyncAudD, AudD, AudDSerializationError, AudDServerError
from audd._source import prepare_source

# ============================================================================
# C1 — File handle is at EOF on retry; we now re-open per attempt.
# ============================================================================

@respx.mock
def test_c1_file_path_re_read_on_retry(tmp_path: Path) -> None:
    """A path-based source must send full bytes on every retry attempt."""
    p = tmp_path / "audio.bin"
    p.write_bytes(b"\xab\xcd" * 100)  # 200 bytes

    attempts: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        attempts.append(body)
        if len(attempts) < 2:
            return httpx.Response(503, json={"status": "error",
                                              "error": {"error_code": 100, "error_message": "x"}})
        return httpx.Response(200, json={"status": "success", "result": None})

    respx.post("https://api.audd.io/").mock(side_effect=handler)
    AudD(api_token="t", backoff_factor=0).recognize(p)

    assert len(attempts) == 2, "should have retried 503 once"
    # Both attempts should carry the file bytes.
    for body in attempts:
        assert b"\xab\xcd" * 100 in body, "file content missing — retry sent empty body"


@respx.mock
def test_c1_filelike_seekable_re_seeks_on_retry() -> None:
    """A seekable file-like source must seek back to its starting position on retry."""
    fl = io.BytesIO(b"\xff" * 50)
    fl.seek(10)  # caller's start position

    attempts: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.read())
        if len(attempts) < 2:
            return httpx.Response(502, json={"status": "error",
                                              "error": {"error_code": 100, "error_message": "x"}})
        return httpx.Response(200, json={"status": "success", "result": None})

    respx.post("https://api.audd.io/").mock(side_effect=handler)
    AudD(api_token="t", backoff_factor=0).recognize(fl)

    assert len(attempts) == 2
    # Both attempts should carry the post-seek content (40 bytes of 0xff).
    for body in attempts:
        # Multipart wrapping varies; just count the 0xff payload length.
        assert body.count(0xFF) >= 40


def test_c1_filelike_unseekable_raises_on_retry_attempt() -> None:
    """Unseekable file-like must surface a clear error if retry is attempted."""
    class Unseekable:
        def __init__(self, data: bytes) -> None:
            self._data = data
            self._read = False
            self.name = "x.mp3"
        def read(self, n: int = -1) -> bytes:
            if self._read:
                return b""
            self._read = True
            return self._data
        def tell(self) -> int:
            raise OSError("not seekable")

    reopen = prepare_source(Unseekable(b"hi"))
    reopen()  # first attempt: succeeds
    with pytest.raises(RuntimeError, match="Cannot retry an unseekable"):
        reopen()  # second attempt: must fail loudly


# ============================================================================
# C3 — Code 51 (deprecation warning) should warn-and-pass-through, not raise.
# ============================================================================

@respx.mock
def test_c3_code_51_with_result_warns_and_returns() -> None:
    """Code 51 + a usable result: emit DeprecationWarning, return the result."""
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(200, json={
            "status": "error",
            "error": {"error_code": 51, "error_message": "deprecated 'foo' parameter"},
            "result": {"timecode": "00:01", "artist": "X", "title": "Y"},
        }),
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = AudD(api_token="t").recognize("https://example.mp3")
    assert result is not None
    assert result.artist == "X"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught), \
        "expected DeprecationWarning to fire"
    assert any("deprecated" in str(w.message) for w in caught)


@respx.mock
def test_c3_code_51_without_result_still_raises() -> None:
    """Code 51 with no usable result: still raise — there's nothing to return."""
    from audd.errors import AudDInvalidRequestError
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(200, json={
            "status": "error",
            "error": {"error_code": 51, "error_message": "deprecated"},
        }),
    )
    with pytest.raises(AudDInvalidRequestError) as ei:
        AudD(api_token="t").recognize("https://example.mp3")
    assert ei.value.error_code == 51


# ============================================================================
# S2 — Non-JSON HTTP errors should map to AudDServerError, not Serialization.
# ============================================================================

@respx.mock
def test_s2_non_json_5xx_raises_server_error_not_serialization_error() -> None:
    """502 with HTML body: user should see the HTTP status, not 'Unparseable'."""
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(502, text="<html><body>Bad gateway</body></html>",
                                     headers={"content-type": "text/html"}),
    )
    with pytest.raises(AudDServerError) as ei:
        AudD(api_token="t", max_retries=1).recognize("https://example.mp3")
    assert ei.value.http_status == 502


@respx.mock
def test_s2_2xx_with_garbage_json_still_raises_serialization_error() -> None:
    """200 with bad JSON: that's a real serialization issue."""
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(200, text="not json at all", headers={"content-type": "text/plain"}),
    )
    with pytest.raises(AudDSerializationError):
        AudD(api_token="t").recognize("https://example.mp3")


# ============================================================================
# M2 — Context manager protocol for AudD / AsyncAudD.
# ============================================================================

@respx.mock
def test_m2_audd_as_context_manager() -> None:
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(200, json={"status": "success", "result": None}),
    )
    with AudD(api_token="t") as audd:
        assert audd.recognize("https://example.mp3") is None
    # After context exit, calling close() again must be safe (idempotent).
    audd.close()


@pytest.mark.asyncio
@respx.mock
async def test_m2_async_audd_as_context_manager() -> None:
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(200, json={"status": "success", "result": None}),
    )
    async with AsyncAudD(api_token="t") as audd:
        assert await audd.recognize("https://example.mp3") is None


# ============================================================================
# S9 — Better error message when string source is neither URL nor file.
# ============================================================================

def test_s9_typo_url_raises_clear_typeerror() -> None:
    """A typo'd URL or non-existent path should raise TypeError with a hint."""
    with pytest.raises(TypeError, match="must start with http"):
        prepare_source("htttps://typo.example.com/song.mp3")


# ============================================================================
# C2 — Advanced namespace uses RECOGNITION retry policy (cost protection).
# ============================================================================

def test_c2_advanced_uses_recognition_policy() -> None:
    """Lyrics search is metered → must use RECOGNITION policy, not READ.

    Inspecting the AudD.advanced setup: confirm it constructs Advanced with
    a RECOGNITION-class policy (not READ). This guards against a regression
    where someone accidentally swaps it back.
    """
    from audd._retry import RetryClass

    audd = AudD(api_token="t")
    advanced = audd.advanced
    assert advanced._read.retry_class == RetryClass.RECOGNITION, (
        "Advanced retries should be RECOGNITION-class to avoid double-billing "
        "post-upload read timeouts"
    )
