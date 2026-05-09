"""Tests for set_api_token rotation — including thread-safety under concurrent recognize() calls."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from audd import AudD


def _extract_api_token(body: bytes) -> str | None:
    """Pull the api_token field out of a urlencoded or multipart body."""
    text = body.decode("utf-8", errors="replace")
    # Multipart form: look for the named-field marker.
    marker = 'name="api_token"\r\n\r\n'
    idx = text.find(marker)
    if idx >= 0:
        after = text[idx + len(marker):]
        return after.split("\r\n", 1)[0]
    # Form-urlencoded.
    parsed = parse_qs(text)
    if "api_token" in parsed:
        return parsed["api_token"][0]
    return None


@respx.mock
def test_set_api_token_rotates_for_subsequent_calls() -> None:
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tok = _extract_api_token(request.read())
        if tok is not None:
            captured.append(tok)
        return httpx.Response(200, json={"status": "success", "result": None})

    respx.post("https://api.audd.io/").mock(side_effect=handler)
    client = AudD(api_token="t-old")
    client.recognize("https://x.mp3")
    client.set_api_token("t-new")
    client.recognize("https://x.mp3")
    client.close()
    assert captured == ["t-old", "t-new"]


def test_set_api_token_rejects_empty() -> None:
    c = AudD(api_token="t-old")
    with pytest.raises(ValueError):
        c.set_api_token("")
    c.close()


@respx.mock
def test_set_api_token_concurrent_rotation_is_safe() -> None:
    """Race rotation against many concurrent recognize() calls; assert no crash
    and that all observed tokens are valid (either old or new)."""
    seen: list[str] = []
    seen_lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        tok = _extract_api_token(request.read())
        if tok is not None:
            with seen_lock:
                seen.append(tok)
        return httpx.Response(200, json={"status": "success", "result": None})

    respx.post("https://api.audd.io/").mock(side_effect=handler)
    client = AudD(api_token="t0")

    def call() -> None:
        client.recognize("https://x.mp3")

    def rotate(label: str) -> None:
        client.set_api_token(label)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(call) for _ in range(40)]
        for i in range(8):
            pool.submit(rotate, f"t{i + 1}")
        for f in futures:
            f.result()

    client.close()
    valid = {f"t{i}" for i in range(9)}
    assert all(t in valid for t in seen)


def test_set_api_token_updates_derive_longpoll_category() -> None:
    """`derive_longpoll_category` must reflect the rotated token."""
    client = AudD(api_token="orig")
    cat_before = client.streams.derive_longpoll_category(7)
    client.set_api_token("new")
    cat_after = client.streams.derive_longpoll_category(7)
    client.close()
    assert cat_before != cat_after
