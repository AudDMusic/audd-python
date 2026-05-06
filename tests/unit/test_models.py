"""Unit tests for typed models — extras, helpers, custom vs public match."""
from __future__ import annotations

from audd.models import (
    AppleMusicMetadata,
    EnterpriseMatch,
    LyricsResult,
    RecognitionResult,
    StreamCallbackPayload,
)


def test_public_match_basic_fields() -> None:
    r = RecognitionResult.model_validate({
        "timecode": "00:56", "artist": "X", "title": "Y", "album": "Z",
        "song_link": "https://lis.tn/abcd",
    })
    assert r.timecode == "00:56"
    assert r.artist == "X"
    assert r.is_public_match is True
    assert r.is_custom_match is False
    assert r.audio_id is None


def test_custom_match_only_audio_id() -> None:
    r = RecognitionResult.model_validate({"timecode": "01:45", "audio_id": 146})
    assert r.is_custom_match is True
    assert r.is_public_match is False
    assert r.artist is None


def test_thumbnail_url_for_lis_tn() -> None:
    r = RecognitionResult.model_validate({
        "timecode": "00:00", "artist": "X", "title": "Y",
        "song_link": "https://lis.tn/abc",
    })
    assert r.thumbnail_url == "https://lis.tn/abc?thumb"


def test_thumbnail_url_for_youtube_returns_none() -> None:
    r = RecognitionResult.model_validate({
        "timecode": "00:00", "artist": "X", "title": "Y",
        "song_link": "https://youtu.be/abc",
    })
    assert r.thumbnail_url is None


def test_thumbnail_url_when_no_song_link() -> None:
    r = RecognitionResult.model_validate({"timecode": "01:45", "audio_id": 7})
    assert r.thumbnail_url is None


def test_thumbnail_url_for_lis_tn_with_existing_query() -> None:
    """Append, don't replace."""
    r = RecognitionResult.model_validate({
        "timecode": "00:00", "artist": "X", "title": "Y",
        "song_link": "https://lis.tn/abc?x=1",
    })
    assert r.thumbnail_url == "https://lis.tn/abc?x=1&thumb"


def test_extras_pass_through() -> None:
    """Unknown fields must be accessible via model_extra."""
    r = RecognitionResult.model_validate({
        "timecode": "00:00", "artist": "X", "title": "Y",
        "tidal": {"id": 999, "url": "https://tidal.com/track/999"},
    })
    assert r.model_extra is not None
    assert r.model_extra["tidal"] == {"id": 999, "url": "https://tidal.com/track/999"}


def test_apple_music_extras() -> None:
    am = AppleMusicMetadata.model_validate({
        "artistName": "X", "name": "Y",
        "isAppleDigitalMaster": True,
    })
    assert am.model_extra is not None
    assert am.model_extra["isAppleDigitalMaster"] is True


def test_enterprise_match_fields() -> None:
    e = EnterpriseMatch.model_validate({
        "score": 100, "timecode": "00:11", "artist": "X", "title": "Y",
        "isrc": "ABC", "upc": "DEF", "start_offset": 1, "end_offset": 8680,
    })
    assert e.score == 100
    assert e.isrc == "ABC"


def test_lyrics_result() -> None:
    lyric = LyricsResult.model_validate({"artist": "X", "title": "Y"})
    assert lyric.artist == "X"


def test_stream_callback_payload_result_envelope() -> None:
    payload = StreamCallbackPayload.parse({
        "status": "success",
        "result": {
            "radio_id": 7, "timestamp": "2020-04-13 10:31:43", "play_length": 111,
            "results": [{"artist": "X", "title": "Y", "score": 100}],
        },
    })
    assert payload.is_result is True
    assert payload.is_notification is False
    assert payload.result is not None
    assert payload.result.radio_id == 7
    assert payload.notification is None


def test_stream_callback_payload_notification() -> None:
    payload = StreamCallbackPayload.parse({
        "status": "-",
        "notification": {
            "radio_id": 3, "stream_running": False,
            "notification_code": 650, "notification_message": "can't connect",
        },
        "time": 1587939136,
    })
    assert payload.is_notification is True
    assert payload.notification is not None
    assert payload.notification.notification_code == 650


def test_recognition_result_repr_includes_song_link() -> None:
    r = RecognitionResult.model_validate({
        "timecode": "00:56",
        "artist": "X",
        "title": "Y",
        "song_link": "https://lis.tn/abcd",
    })
    rep = repr(r)
    assert rep.startswith("<RecognitionResult ")
    assert rep.endswith(">")
    assert "artist='X'" in rep
    assert "title='Y'" in rep
    assert "timecode='00:56'" in rep
    assert "song_link='https://lis.tn/abcd'" in rep


def test_recognition_result_repr_omits_missing_fields() -> None:
    r = RecognitionResult.model_validate({"timecode": "01:45", "audio_id": 7})
    rep = repr(r)
    assert "artist" not in rep
    assert "title" not in rep
    assert "song_link" not in rep
    assert "timecode='01:45'" in rep


def test_recognition_result_pretty_print_includes_extras() -> None:
    import io
    r = RecognitionResult.model_validate({
        "timecode": "00:56",
        "artist": "X",
        "title": "Y",
        "song_link": "https://lis.tn/abcd",
        "weird_unknown_field": {"nested": True},
    })
    buf = io.StringIO()
    r.pretty_print(stream=buf)
    out = buf.getvalue()
    assert '"artist": "X"' in out
    assert '"weird_unknown_field"' in out
    assert out.endswith("\n")


def test_enterprise_match_repr_and_pretty_print() -> None:
    import io
    m = EnterpriseMatch.model_validate({
        "score": 92, "timecode": "00:13", "artist": "X", "title": "Y",
        "song_link": "https://lis.tn/zzz",
    })
    rep = repr(m)
    assert rep.startswith("<EnterpriseMatch ")
    assert "score=92" in rep
    assert "song_link='https://lis.tn/zzz'" in rep
    buf = io.StringIO()
    m.pretty_print(stream=buf)
    assert '"score": 92' in buf.getvalue()
