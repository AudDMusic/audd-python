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
