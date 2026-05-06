"""Unit tests for the pure helpers."""
from __future__ import annotations

import pytest

from audd._callbacks import (
    DuplicateReturnParameterError,
    add_return_to_url,
    derive_longpoll_category,
    parse_callback,
)


def test_derive_longpoll_category_known_pair() -> None:
    """First 9 hex chars of MD5(MD5(api_token) + str(radio_id))."""
    cat = derive_longpoll_category("d29ebb205488e3b414bcc0c50432463e", 1)
    assert cat == "088719f57"


def test_derive_longpoll_category_returns_9_hex_chars() -> None:
    cat = derive_longpoll_category("any-token", 999)
    assert len(cat) == 9
    assert all(c in "0123456789abcdef" for c in cat)


def test_parse_callback_recognition() -> None:
    p = parse_callback({
        "status": "success",
        "result": {"radio_id": 7, "results": [{"artist": "X", "title": "Y", "score": 100}]},
    })
    assert p.is_result
    assert p.result is not None
    assert p.result.radio_id == 7


def test_parse_callback_notification() -> None:
    p = parse_callback({
        "status": "-",
        "notification": {
            "radio_id": 3, "stream_running": False,
            "notification_code": 0, "notification_message": "ok",
        },
        "time": 1234,
    })
    assert p.is_notification
    assert p.time == 1234


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
