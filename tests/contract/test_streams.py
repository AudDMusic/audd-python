"""Stream-management fixtures."""
from __future__ import annotations

from audd._callbacks import parse_callback


def test_get_streams_empty(load_fixture) -> None:
    payload = load_fixture("getStreams_empty.json")
    assert payload["status"] == "success"
    assert payload["result"] == []


def test_callback_with_result(load_fixture) -> None:
    payload = load_fixture("streams_callback_with_result.json")
    match, notif = parse_callback(payload)
    assert notif is None
    assert match is not None
    assert match.radio_id == 7
    assert match.song.artist  # top match present


def test_callback_with_notification(load_fixture) -> None:
    payload = load_fixture("streams_callback_with_notification.json")
    match, notif = parse_callback(payload)
    assert match is None
    assert notif is not None
    assert notif.notification_code == 650
