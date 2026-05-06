"""Stream-management fixtures."""
from __future__ import annotations

from audd._callbacks import parse_callback


def test_get_streams_empty(load_fixture) -> None:
    payload = load_fixture("getStreams_empty.json")
    assert payload["status"] == "success"
    assert payload["result"] == []


def test_callback_with_result(load_fixture) -> None:
    payload = load_fixture("streams_callback_with_result.json")
    parsed = parse_callback(payload)
    assert parsed.is_result
    assert parsed.result is not None
    assert parsed.result.radio_id == 7


def test_callback_with_notification(load_fixture) -> None:
    payload = load_fixture("streams_callback_with_notification.json")
    parsed = parse_callback(payload)
    assert parsed.is_notification
    assert parsed.notification is not None
    assert parsed.notification.notification_code == 650
