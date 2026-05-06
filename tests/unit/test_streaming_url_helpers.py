"""Tests for streaming-URL helpers on RecognitionResult / EnterpriseMatch.

Covers ``streaming_url(provider)``, ``streaming_urls()``, ``preview_url()``,
and the metadata-fallback chain when ``song_link`` is not a lis.tn URL.
"""
from __future__ import annotations

from typing import Any

import pytest

from audd.models import EnterpriseMatch, RecognitionResult


def _result(song_link: str | None = None, **extra: Any) -> RecognitionResult:
    return RecognitionResult.model_validate({"timecode": "00:01", "song_link": song_link, **extra})


# ---- streaming_url: lis.tn redirect path ------------------------------------


def test_streaming_url_returns_redirect_for_lis_tn() -> None:
    r = _result("https://lis.tn/abc")
    assert r.streaming_url("spotify") == "https://lis.tn/abc?spotify"
    assert r.streaming_url("apple_music") == "https://lis.tn/abc?apple_music"
    assert r.streaming_url("deezer") == "https://lis.tn/abc?deezer"
    assert r.streaming_url("napster") == "https://lis.tn/abc?napster"
    assert r.streaming_url("youtube") == "https://lis.tn/abc?youtube"


def test_streaming_url_returns_none_for_youtube_song_link_with_no_metadata() -> None:
    r = _result("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert r.streaming_url("spotify") is None


def test_streaming_url_returns_none_when_song_link_absent() -> None:
    r = _result(None)
    assert r.streaming_url("spotify") is None


# ---- streaming_url: metadata-fallback chain ---------------------------------


def test_streaming_url_falls_back_to_apple_music_url_for_youtube_song_link() -> None:
    r = _result(
        "https://www.youtube.com/watch?v=x",
        apple_music={"url": "https://music.apple.com/us/album/x/123?i=456"},
    )
    assert r.streaming_url("apple_music") == "https://music.apple.com/us/album/x/123?i=456"


def test_streaming_url_falls_back_to_spotify_external_url_for_youtube_song_link() -> None:
    r = _result(
        "https://www.youtube.com/watch?v=x",
        spotify={"external_urls": {"spotify": "https://open.spotify.com/track/abc"}},
    )
    assert r.streaming_url("spotify") == "https://open.spotify.com/track/abc"


def test_streaming_url_falls_back_to_deezer_link_for_youtube_song_link() -> None:
    r = _result(
        "https://www.youtube.com/watch?v=x",
        deezer={"link": "https://www.deezer.com/track/123"},
    )
    assert r.streaming_url("deezer") == "https://www.deezer.com/track/123"


def test_streaming_url_falls_back_to_napster_href_for_youtube_song_link() -> None:
    r = _result(
        "https://www.youtube.com/watch?v=x",
        napster={"href": "https://api.napster.com/v2.2/tracks/x"},
    )
    assert r.streaming_url("napster") == "https://api.napster.com/v2.2/tracks/x"


def test_streaming_url_prefers_direct_url_over_lis_tn_redirect() -> None:
    """When metadata has a direct URL AND song_link is lis.tn, prefer direct."""
    r = _result(
        "https://lis.tn/abc",
        apple_music={"url": "https://music.apple.com/us/album/x/123?i=456"},
    )
    # Direct URL is preferred; the lis.tn redirect (?apple_music) is the fallback.
    assert r.streaming_url("apple_music") == "https://music.apple.com/us/album/x/123?i=456"


def test_streaming_urls_includes_metadata_fallback_for_youtube_song_link() -> None:
    r = _result(
        "https://www.youtube.com/watch?v=x",
        apple_music={"url": "https://music.apple.com/us/album/x/123"},
        deezer={"link": "https://www.deezer.com/track/123"},
    )
    urls = r.streaming_urls()
    assert urls["apple_music"] == "https://music.apple.com/us/album/x/123"
    assert urls["deezer"] == "https://www.deezer.com/track/123"
    # spotify/napster/youtube absent — no metadata + non-lis.tn song_link.
    assert "spotify" not in urls
    assert "youtube" not in urls


def test_streaming_url_appends_amp_when_link_has_query() -> None:
    r = _result("https://lis.tn/abc?ref=foo")
    assert r.streaming_url("spotify") == "https://lis.tn/abc?ref=foo&spotify"


def test_streaming_url_invalid_provider_raises() -> None:
    r = _result("https://lis.tn/abc")
    with pytest.raises(ValueError):
        r.streaming_url("tidal")  # type: ignore[arg-type]


def test_streaming_urls_returns_all_five() -> None:
    r = _result("https://lis.tn/abc")
    urls = r.streaming_urls()
    assert set(urls.keys()) == {"spotify", "apple_music", "deezer", "napster", "youtube"}
    assert urls["spotify"] == "https://lis.tn/abc?spotify"


def test_streaming_urls_empty_when_song_link_off_lis_tn() -> None:
    r = _result("https://www.youtube.com/watch?v=x")
    assert r.streaming_urls() == {}


def test_streaming_urls_empty_when_song_link_missing() -> None:
    assert _result(None).streaming_urls() == {}


# ---- preview_url -----------------------------------------------------------


def test_preview_url_picks_apple_music_first() -> None:
    r = _result(
        apple_music={"previews": [{"url": "https://itunes/preview.m4a"}]},
        spotify={"preview_url": "https://spotify/preview.mp3"},
        deezer={"preview": "https://deezer/preview.mp3"},
    )
    assert r.preview_url() == "https://itunes/preview.m4a"


def test_preview_url_falls_through_to_spotify() -> None:
    r = _result(
        spotify={"preview_url": "https://spotify/preview.mp3"},
        deezer={"preview": "https://deezer/preview.mp3"},
    )
    assert r.preview_url() == "https://spotify/preview.mp3"


def test_preview_url_falls_through_to_deezer() -> None:
    r = _result(deezer={"preview": "https://deezer/preview.mp3"})
    assert r.preview_url() == "https://deezer/preview.mp3"


def test_preview_url_returns_none_when_no_provider_has_preview() -> None:
    r = _result(apple_music={}, spotify={}, deezer={})
    assert r.preview_url() is None


def test_preview_url_returns_none_when_no_metadata() -> None:
    assert _result(None).preview_url() is None


# ---- EnterpriseMatch parity ------------------------------------------------


def test_enterprise_match_streaming_helpers() -> None:
    m = EnterpriseMatch.model_validate({
        "score": 90, "timecode": "00:11", "song_link": "https://lis.tn/xyz",
    })
    assert m.streaming_url("spotify") == "https://lis.tn/xyz?spotify"
    assert "deezer" in m.streaming_urls()
    assert m.thumbnail_url == "https://lis.tn/xyz?thumb"
