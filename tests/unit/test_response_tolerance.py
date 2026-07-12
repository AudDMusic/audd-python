"""Lenient response parsing: wrong-typed scalar fields are coerced when
convertible and degrade to None when they aren't — parsing never raises.

Covers recognize, enterprise, streams, and callbacks, plus the error-envelope
guards (string ``error`` blocks, non-numeric ``error_code``).
"""
from __future__ import annotations

import httpx
import pytest
import respx

from audd import AsyncAudD, AudD
from audd.errors import (
    AudDAuthenticationError,
    AudDServerError,
    raise_from_error_response,
)
from audd.models import EnterpriseMatch, RecognitionResult, Stream

# ============================================================================
# recognize — wrong-typed fields degrade per-field, the match survives.
# ============================================================================

_WRONG_TYPED_RESULT = {
    "artist": 123,
    "title": "Song",
    "album": ["not", "a", "string"],
    "audio_id": "abc",
    "timecode": {"minute": 1},
    "apple_music": "oops",
    "spotify": {"name": "SpotName", "duration_ms": "xx"},
    "musicbrainz": "not-a-list",
    "song_link": "https://lis.tn/abcd",
}


def _assert_degraded(result: RecognitionResult | None) -> None:
    assert result is not None
    assert result.artist == "123"  # number for a str field: coerced, not dropped
    assert result.title == "Song"  # well-typed fields survive
    assert result.album is None  # was a list
    assert result.audio_id is None  # was "abc"
    assert result.timecode is None  # was an object
    assert result.apple_music is None  # was "oops"
    # Nested wrong-typed field degrades inside the block; the block survives.
    assert result.spotify is not None
    assert result.spotify.name == "SpotName"
    assert result.spotify.duration_ms is None  # was "xx"
    assert result.musicbrainz == []  # was "not-a-list"
    assert result.song_link == "https://lis.tn/abcd"


def test_model_validate_tolerates_wrong_typed_fields() -> None:
    _assert_degraded(RecognitionResult.model_validate(_WRONG_TYPED_RESULT))


@respx.mock
def test_recognize_tolerates_wrong_typed_fields() -> None:
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "result": _WRONG_TYPED_RESULT},
        ),
    )
    _assert_degraded(AudD(api_token="t").recognize("https://example.mp3"))


@pytest.mark.asyncio
@respx.mock
async def test_async_recognize_tolerates_wrong_typed_fields() -> None:
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "result": _WRONG_TYPED_RESULT},
        ),
    )
    async with AsyncAudD(api_token="t") as audd:
        _assert_degraded(await audd.recognize("https://example.mp3"))


def test_numeric_string_coercions_still_work() -> None:
    """Lax coercion is kept: numeric strings for int fields still parse."""
    r = RecognitionResult.model_validate({"audio_id": "42", "artist": "A"})
    assert r.audio_id == 42
    assert r.is_custom_match


# ============================================================================
# scalar coercion — wrong-typed scalars are converted when convertible,
# and degrade to None only when they aren't.
# ============================================================================


def test_convertible_scalars_coerce() -> None:
    m = EnterpriseMatch.model_validate({
        "score": "85",            # numeric string → int
        "start_offset": 1500.9,   # float → truncated int
        "end_offset": "1e3",      # scientific-notation string → int
        "artist": 123,            # number → str
        "title": 8.5,             # float → str
        "timecode": 123,          # number → str (timecode is str-typed)
        "start_seconds": " 3.5 ",  # numeric string (trimmed) → float
        "end_seconds": 7,         # int → float
    })
    assert m.score == 85
    assert m.start_offset == 1500
    assert m.end_offset == 1000
    assert m.artist == "123"
    assert m.title == "8.5"
    assert m.timecode == "123"
    assert m.start_seconds == 3.5
    assert m.end_seconds == 7.0


def test_non_convertible_scalars_degrade_to_none() -> None:
    m = EnterpriseMatch.model_validate({
        "score": "abc",           # non-numeric string
        "start_offset": "85abc",  # partial-numeric strings don't parse
        "end_offset": "0x1A",     # hex is not a plain/scientific decimal
        "start_seconds": "NaN",
        "end_seconds": "Infinity",
        "artist": ["unexpected"],
        "label": {"k": "v"},
    })
    assert m.score is None
    assert m.start_offset is None
    assert m.end_offset is None
    assert m.start_seconds is None
    assert m.end_seconds is None
    assert m.artist is None
    assert m.label is None


def test_bool_coercion_for_numeric_fields() -> None:
    m = EnterpriseMatch.model_validate({"score": True, "start_offset": False})
    assert m.score == 1
    assert m.start_offset == 0


def test_bool_for_float_field_degrades_to_none() -> None:
    m = EnterpriseMatch.model_validate({"start_seconds": True})
    assert m.start_seconds is None


@pytest.mark.parametrize("raw", ["true", "1", "yes", "on", " TRUE ", "Yes", "ON"])
def test_bool_string_whitelist_true(raw: str) -> None:
    s = Stream.model_validate({"stream_running": raw})
    assert s.stream_running is True


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "", " FALSE ", "No", "OFF"])
def test_bool_string_whitelist_false(raw: str) -> None:
    s = Stream.model_validate({"stream_running": raw})
    assert s.stream_running is False


@pytest.mark.parametrize("raw", ["maybe", "enabled", "2ish", "null", "t", "y"])
def test_bool_unrecognized_string_degrades_to_none(raw: str) -> None:
    s = Stream.model_validate({"stream_running": raw})
    assert s.stream_running is None


def test_bool_from_numbers() -> None:
    assert Stream.model_validate({"stream_running": 5}).stream_running is True
    assert Stream.model_validate({"stream_running": -1}).stream_running is True
    assert Stream.model_validate({"stream_running": 0}).stream_running is False
    assert Stream.model_validate({"stream_running": 0.0}).stream_running is False


def test_overflowing_numeric_strings_never_produce_garbage() -> None:
    m = EnterpriseMatch.model_validate({"score": "1e999", "start_seconds": "1e999"})
    assert m.score is None  # not a garbage 0 / huge saturated int
    assert m.start_seconds is None  # not inf


# ============================================================================
# enterprise — a wrong-typed field degrades; the song is NOT dropped.
# ============================================================================


@respx.mock
def test_enterprise_wrong_typed_field_keeps_the_match() -> None:
    respx.post("https://enterprise.audd.io/").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "result": [
                {
                    "songs": [
                        {"artist": "A", "title": "T", "score": "high",
                         "start_offset": "bad", "end_offset": 2000},
                    ],
                    "offset": "01:02:03",
                },
                # Whole chunk of the wrong shape: skipped, not fatal.
                "garbage-chunk",
                {"songs": "not-a-list", "offset": "00:10"},
            ],
        }),
    )
    matches = AudD(api_token="t").recognize_enterprise("https://x.mp3", limit=1)
    assert len(matches) == 1
    m = matches[0]
    assert m.artist == "A"
    assert m.title == "T"
    assert m.score is None  # was "high"
    assert m.start_offset is None  # was "bad"
    assert m.end_offset == 2000
    # Absolute file position still computed from the >1h chunk offset.
    assert m.start_seconds == 3723.0
    assert m.end_seconds == 3725.0


# ============================================================================
# streams — getStreams entries with wrong-typed fields degrade per-field.
# ============================================================================


@respx.mock
def test_streams_list_tolerates_wrong_typed_fields() -> None:
    respx.post("https://api.audd.io/getStreams/").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "result": [
                {"radio_id": {"nested": True}, "url": 5,
                 "stream_running": "maybe", "longpoll_category": "abc123def"},
            ],
        }),
    )
    streams = AudD(api_token="t").streams.list()
    assert len(streams) == 1
    s = streams[0]
    assert s.radio_id is None  # object where an int belongs: not convertible
    assert s.url == "5"  # number for a str field: coerced
    assert s.stream_running is None  # "maybe" is outside the bool whitelist
    assert s.longpoll_category == "abc123def"


# ============================================================================
# callbacks — wrong-typed song/notification fields degrade, parse succeeds.
# ============================================================================


def test_parse_callback_match_tolerates_wrong_typed_fields() -> None:
    match, notif = AudD(api_token="t").streams.parse_callback({
        "result": {
            "radio_id": "not-an-int",
            "timestamp": "2024-01-01 00:00:00",
            "play_length": "xx",
            "results": [{"artist": {"weird": 1}, "title": "T", "score": 77}],
        },
    })
    assert notif is None
    assert match is not None
    assert match.radio_id is None
    assert match.play_length is None
    assert match.song is not None
    assert match.song.artist is None
    assert match.song.title == "T"
    assert match.song.score == 77


def test_parse_callback_notification_tolerates_wrong_typed_fields() -> None:
    match, notif = AudD(api_token="t").streams.parse_callback({
        "notification": {
            "radio_id": "abc",
            "stream_running": 5,
            "notification_code": {"code": 1},
            "notification_message": "stream stopped",
        },
        "time": 1700000000,
    })
    assert match is None
    assert notif is not None
    assert notif.radio_id is None
    assert notif.stream_running is True  # number for a bool field: != 0
    assert notif.notification_code is None
    assert notif.notification_message == "stream stopped"
    assert notif.time == 1700000000


# ============================================================================
# error envelopes — string `error` blocks and non-numeric codes never crash.
# ============================================================================


@respx.mock
def test_error_as_bare_string_raises_typed_error() -> None:
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(
            200, json={"status": "error", "error": "temporarily unavailable"},
        ),
    )
    with pytest.raises(AudDServerError) as ei:
        AudD(api_token="t").recognize("https://x.mp3")
    assert ei.value.error_code == 0
    assert "temporarily unavailable" in str(ei.value)


@respx.mock
def test_string_error_on_success_body_is_ignored() -> None:
    """A stray string `error` alongside status=success must not crash decoding."""
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "error": "notice: something minor",
            "result": {"artist": "A", "title": "T"},
        }),
    )
    result = AudD(api_token="t").recognize("https://x.mp3")
    assert result is not None
    assert result.artist == "A"


def test_error_code_non_numeric_string_defaults_to_zero() -> None:
    with pytest.raises(AudDServerError) as ei:
        raise_from_error_response(
            {"status": "error", "error": {"error_code": "abc", "error_message": "m"}},
            http_status=200, request_id=None,
        )
    assert ei.value.error_code == 0


def test_error_code_none_defaults_to_zero() -> None:
    with pytest.raises(AudDServerError) as ei:
        raise_from_error_response(
            {"status": "error", "error": {"error_code": None, "error_message": "m"}},
            http_status=200, request_id=None,
        )
    assert ei.value.error_code == 0


def test_error_code_numeric_string_still_maps() -> None:
    with pytest.raises(AudDAuthenticationError) as ei:
        raise_from_error_response(
            {"status": "error", "error": {"error_code": "901", "error_message": "m"}},
            http_status=200, request_id=None,
        )
    assert ei.value.error_code == 901
