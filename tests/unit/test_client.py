"""Unit tests for AudD / AsyncAudD top-level recognize methods."""
from __future__ import annotations

import io

import httpx
import pytest
import respx

from audd import AsyncAudD, AudD
from audd.errors import AudDAuthenticationError, AudDSubscriptionError
from audd.models import EnterpriseMatch, RecognitionResult


@respx.mock
def test_recognize_by_url_returns_typed_result() -> None:
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "result": {
                "timecode": "00:56", "artist": "X", "title": "Y", "album": "Z",
                "song_link": "https://lis.tn/abc",
            },
        }),
    )
    client = AudD(api_token="t-test")
    result = client.recognize("https://example.mp3")
    assert isinstance(result, RecognitionResult)
    assert result.artist == "X"
    assert result.thumbnail_url == "https://lis.tn/abc?thumb"


@respx.mock
def test_recognize_no_match_returns_none() -> None:
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(200, json={"status": "success", "result": None}),
    )
    client = AudD(api_token="t-test")
    assert client.recognize("https://example.mp3") is None


@respx.mock
def test_recognize_error_900_raises_auth() -> None:
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(200, json={
            "status": "error",
            "error": {"error_code": 900, "error_message": "bad token"},
            "request_params": {"api_token": "t***t"},
        }),
    )
    client = AudD(api_token="t-test")
    with pytest.raises(AudDAuthenticationError) as ei:
        client.recognize("https://example.mp3")
    assert ei.value.error_code == 900


@respx.mock
def test_recognize_with_return_metadata_param() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"status": "success", "result": None})

    respx.post("https://api.audd.io/").mock(side_effect=handler)
    AudD(api_token="t-test").recognize("https://example.mp3", return_metadata=["apple_music", "spotify"])
    assert "return=apple_music%2Cspotify" in captured["body"]


@respx.mock
def test_recognize_extra_parameters_propagated() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"status": "success", "result": None})

    respx.post("https://api.audd.io/").mock(side_effect=handler)
    AudD(api_token="t-test").recognize(
        "https://example.mp3",
        return_metadata="apple_music",
        extra_parameters={"foo": "bar", "return": "ignored"},
    )
    assert "foo=bar" in captured["body"]
    # Typed return_metadata wins over extras["return"] on collision.
    assert "return=apple_music" in captured["body"]
    assert "return=ignored" not in captured["body"]


@respx.mock
def test_recognize_enterprise_extra_parameters_propagated() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"status": "success", "result": []})

    respx.post("https://enterprise.audd.io/").mock(side_effect=handler)
    AudD(api_token="t-test").recognize_enterprise(
        "https://example.mp3",
        limit=2,
        extra_parameters={"foo": "bar", "limit": "999"},
    )
    assert "foo=bar" in captured["body"]
    assert "limit=2" in captured["body"]
    assert "limit=999" not in captured["body"]


@respx.mock
def test_recognize_uploads_file_via_multipart() -> None:
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(200, json={"status": "success", "result": None}),
    )
    fake = io.BytesIO(b"\x00\x01\x02")
    fake.name = "song.mp3"  # type: ignore[attr-defined]
    AudD(api_token="t-test").recognize(fake)
    sent = respx.calls.last.request
    assert b"multipart/form-data" in sent.headers["content-type"].encode()


@respx.mock
def test_recognize_enterprise_returns_list_of_matches() -> None:
    respx.post("https://enterprise.audd.io/").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "result": [{
                "songs": [{
                    "score": 100, "timecode": "00:11",
                    "artist": "X", "title": "Y", "song_link": "https://lis.tn/abc",
                }],
                "offset": "00:00",
            }],
            "execution_time": "1.0s",
        }),
    )
    matches = AudD(api_token="t-test").recognize_enterprise("https://example.mp3", limit=1)
    assert isinstance(matches, list)
    assert len(matches) == 1
    assert isinstance(matches[0], EnterpriseMatch)
    assert matches[0].artist == "X"


@respx.mock
def test_recognize_enterprise_anchors_offsets_to_file() -> None:
    """The flattened matches expose file-absolute start_seconds/end_seconds,
    derived from the chunk offset + the in-fragment start_offset/end_offset.
    The chunk offset is the only source of file position and must not be lost."""
    respx.post("https://enterprise.audd.io/").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "result": [
                {"offset": "00:01:00", "songs": [{
                    "artist": "A", "title": "T",
                    "start_offset": 4200, "end_offset": 11800,
                }]},
                {"offset": None, "songs": [{"artist": "B", "title": "U"}]},
            ],
        }),
    )
    matches = AudD(api_token="t-test").recognize_enterprise("https://example.mp3", limit=2)
    assert matches[0].start_seconds == 64.2   # 60 + 4200/1000
    assert matches[0].end_seconds == 71.8     # 60 + 11800/1000
    assert matches[1].start_seconds is None    # chunk offset absent → unknown
    assert matches[1].end_seconds is None


@respx.mock
def test_recognize_enterprise_requests_accurate_offsets_by_default() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"status": "success", "result": []})

    respx.post("https://enterprise.audd.io/").mock(side_effect=handler)
    AudD(api_token="t-test").recognize_enterprise("https://example.mp3", limit=1)
    assert "accurate_offsets=true" in captured["body"]


@respx.mock
def test_recognize_enterprise_song_without_score_parses() -> None:
    """Enterprise endpoint legitimately returns songs with no score (and no
    isrc/upc/label) — e.g. YouTube-sourced entries. The decode path must not
    raise; the match parses with score is None."""
    respx.post("https://enterprise.audd.io/").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "result": [{
                "songs": [{
                    "timecode": "00:11", "artist": "X", "title": "Y",
                    "song_link": "https://youtu.be/abc",
                }],
                "offset": "00:00",
            }],
        }),
    )
    matches = AudD(api_token="t-test").recognize_enterprise("https://example.mp3", limit=1)
    assert len(matches) == 1
    assert matches[0].score is None
    assert matches[0].isrc is None
    assert matches[0].artist == "X"


@respx.mock
def test_recognize_missing_timecode_parses() -> None:
    """A recognition result missing timecode must parse, not raise."""
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "result": {"artist": "X", "title": "Y"},
        }),
    )
    result = AudD(api_token="t-test").recognize("https://example.mp3")
    assert isinstance(result, RecognitionResult)
    assert result.timecode is None
    assert result.artist == "X"


@respx.mock
def test_recognize_enterprise_unauthorized_raises_subscription_error() -> None:
    respx.post("https://enterprise.audd.io/").mock(
        return_value=httpx.Response(200, json={
            "status": "error",
            "error": {"error_code": 904, "error_message": "denied"},
        }),
    )
    with pytest.raises(AudDSubscriptionError):
        AudD(api_token="t-test").recognize_enterprise("https://example.mp3", limit=1)


@pytest.mark.asyncio
@respx.mock
async def test_async_recognize() -> None:
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "result": {"timecode": "00:01", "artist": "X", "title": "Y"},
        }),
    )
    client = AsyncAudD(api_token="t-test")
    result = await client.recognize("https://example.mp3")
    await client.aclose()
    assert result is not None
    assert result.artist == "X"


@respx.mock
def test_request_id_surfaces_on_exception() -> None:
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(
            200,
            json={"status": "error", "error": {"error_code": 900, "error_message": "x"}},
            headers={"x-request-id": "rid-42"},
        ),
    )
    with pytest.raises(AudDAuthenticationError) as ei:
        AudD(api_token="t-test").recognize("https://example.mp3")
    assert ei.value.request_id == "rid-42"
