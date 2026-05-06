"""Unit tests for the pure helpers."""
from __future__ import annotations

import json

import pytest

from audd._callbacks import (
    DuplicateReturnParameterError,
    add_return_to_url,
    derive_longpoll_category,
    parse_callback,
)
from audd.errors import AudDSerializationError


def test_derive_longpoll_category_known_pair() -> None:
    """First 9 hex chars of MD5(MD5(api_token) + str(radio_id))."""
    cat = derive_longpoll_category("d29ebb205488e3b414bcc0c50432463e", 1)
    assert cat == "088719f57"


def test_derive_longpoll_category_returns_9_hex_chars() -> None:
    cat = derive_longpoll_category("any-token", 999)
    assert len(cat) == 9
    assert all(c in "0123456789abcdef" for c in cat)


def test_parse_callback_recognition() -> None:
    match, notif = parse_callback({
        "status": "success",
        "result": {"radio_id": 7, "results": [{"artist": "X", "title": "Y", "score": 100}]},
    })
    assert notif is None
    assert match is not None
    assert match.radio_id == 7
    assert match.song.artist == "X"
    assert match.song.title == "Y"
    assert match.song.score == 100
    assert match.alternatives == []


def test_parse_callback_recognition_with_alternatives() -> None:
    match, _ = parse_callback({
        "status": "success",
        "result": {
            "radio_id": 7,
            "results": [
                {"artist": "A", "title": "T", "score": 100},
                {"artist": "A2", "title": "T2", "score": 80},
            ],
        },
    })
    assert match is not None
    assert match.song.artist == "A"
    assert len(match.alternatives) == 1
    assert match.alternatives[0].artist == "A2"


def test_parse_callback_notification() -> None:
    match, notif = parse_callback({
        "status": "-",
        "notification": {
            "radio_id": 3, "stream_running": False,
            "notification_code": 0, "notification_message": "ok",
        },
        "time": 1234,
    })
    assert match is None
    assert notif is not None
    assert notif.radio_id == 3
    assert notif.time == 1234


def test_parse_callback_accepts_bytes() -> None:
    body = json.dumps({
        "status": "success",
        "result": {"radio_id": 1, "results": [{"artist": "A", "title": "B", "score": 99}]},
    }).encode()
    match, notif = parse_callback(body)
    assert notif is None
    assert match is not None
    assert match.song.title == "B"


def test_parse_callback_accepts_str() -> None:
    body = json.dumps({
        "status": "success",
        "result": {"radio_id": 1, "results": [{"artist": "A", "title": "B", "score": 99}]},
    })
    match, _ = parse_callback(body)
    assert match is not None


def test_parse_callback_bad_json_raises_serialization() -> None:
    with pytest.raises(AudDSerializationError):
        parse_callback(b"not json")


def test_parse_callback_neither_block_raises_serialization() -> None:
    with pytest.raises(AudDSerializationError) as ei:
        parse_callback({"foo": "bar"})
    assert "neither" in str(ei.value)


def test_parse_callback_empty_results_raises_serialization() -> None:
    with pytest.raises(AudDSerializationError):
        parse_callback({"status": "success", "result": {"radio_id": 1, "results": []}})


def test_add_return_to_url_basic() -> None:
    url = add_return_to_url("https://x.com/cb", "apple_music,deezer")
    assert url == "https://x.com/cb?return=apple_music%2Cdeezer"


def test_add_return_to_url_with_existing_query() -> None:
    url = add_return_to_url("https://x.com/cb?other=1", "spotify")
    assert "other=1" in url
    assert "return=spotify" in url


def test_add_return_to_url_raises_on_duplicate() -> None:
    with pytest.raises(DuplicateReturnParameterError):
        add_return_to_url("https://x.com/cb?return=spotify", "apple_music")


def test_add_return_to_url_no_metadata_passes_through() -> None:
    url = add_return_to_url("https://x.com/cb?other=1", None)
    assert url == "https://x.com/cb?other=1"
