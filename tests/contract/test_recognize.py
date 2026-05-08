"""Recognize against audd-openapi fixtures."""
from __future__ import annotations

from audd.models import RecognitionResult


def test_recognize_basic(load_fixture) -> None:
    payload = load_fixture("recognize_basic.json")
    assert payload["status"] == "success"
    result = RecognitionResult.model_validate(payload["result"])
    assert result.artist
    assert result.timecode
    assert result.is_public_match


def test_recognize_with_metadata(load_fixture) -> None:
    payload = load_fixture("recognize_with_metadata.json")
    result = RecognitionResult.model_validate(payload["result"])
    assert result.apple_music is not None
    assert result.spotify is not None or result.musicbrainz is not None


def test_recognize_custom_match(load_fixture) -> None:
    payload = load_fixture("recognize_custom_match.json")
    result = RecognitionResult.model_validate(payload["result"])
    assert result.is_custom_match
    assert result.audio_id is not None
    assert result.artist is None
