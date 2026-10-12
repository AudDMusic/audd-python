"""Enterprise endpoint fixtures."""
from __future__ import annotations

from audd.models import EnterpriseChunkResult


def test_enterprise_with_isrc_upc(load_fixture) -> None:
    payload = load_fixture("enterprise_with_isrc_upc.json")
    assert payload["status"] == "success"
    chunks = [EnterpriseChunkResult.model_validate(c) for c in payload["result"]]
    assert chunks
    songs = chunks[0].songs
    assert songs
    s = songs[0]
    assert s.isrc
    assert s.upc
    assert s.score >= 0
    assert s.audio_id is None
    assert s.is_public_match
    assert not s.is_custom_match


def test_enterprise_custom_match(load_fixture) -> None:
    payload = load_fixture("enterprise_custom_match.json")
    assert payload["status"] == "success"
    chunks = [EnterpriseChunkResult.model_validate(c) for c in payload["result"]]
    s = chunks[0].songs[0]
    assert s.audio_id == 146
    assert s.is_custom_match
    assert not s.is_public_match
    assert s.artist is None
