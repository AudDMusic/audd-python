"""Unit tests for advanced namespace."""
from __future__ import annotations

import httpx
import respx

from audd import AudD


@respx.mock
def test_find_lyrics_returns_typed_list() -> None:
    respx.post("https://api.audd.io/findLyrics/").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "result": [{"artist": "X", "title": "Y", "lyrics": "..."}],
        }),
    )
    out = AudD(api_token="t").advanced.find_lyrics("rule the world")
    assert len(out) == 1
    assert out[0].artist == "X"
    assert out[0].lyrics == "..."


@respx.mock
def test_raw_request_returns_dict() -> None:
    respx.post("https://api.audd.io/someNewMethod/").mock(
        return_value=httpx.Response(200, json={"status": "success", "result": {"foo": "bar"}}),
    )
    out = AudD(api_token="t").advanced.raw_request("someNewMethod", {"q": "hello"})
    assert out == {"status": "success", "result": {"foo": "bar"}}
