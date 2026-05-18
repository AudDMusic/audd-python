"""Live-API smoke tests. Run only when AUDD_API_TOKEN is set."""
from __future__ import annotations

import os

import pytest

from audd import AudD

EXAMPLE_MP3 = "https://audd.tech/example.mp3"


@pytest.mark.integration
def test_recognize_live() -> None:
    token = os.environ["AUDD_API_TOKEN"]
    client = AudD(api_token=token)
    result = client.recognize(EXAMPLE_MP3)
    assert result is not None
    assert result.artist
    assert result.title


@pytest.mark.integration
def test_recognize_with_metadata_live() -> None:
    token = os.environ["AUDD_API_TOKEN"]
    client = AudD(api_token=token)
    result = client.recognize(EXAMPLE_MP3, return_metadata=["apple_music"])
    assert result is not None
    assert result.apple_music is not None
