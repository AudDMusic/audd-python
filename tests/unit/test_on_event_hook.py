"""Tests for the on_event inspection hook — request/response/exception lifecycle."""
from __future__ import annotations

import httpx
import pytest
import respx

from audd import AudD, AudDConnectionError
from audd.client import AudDEvent


@respx.mock
def test_on_event_emits_request_then_response() -> None:
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "result": None},
            headers={"x-request-id": "rid-99"},
        ),
    )
    events: list[AudDEvent] = []
    secret = "secret-token-do-not-leak-12345"
    client = AudD(api_token=secret, on_event=events.append)
    client.recognize("https://x.mp3")
    client.close()
    kinds = [e.kind for e in events]
    assert "request" in kinds and "response" in kinds
    resp_event = next(e for e in events if e.kind == "response")
    assert resp_event.http_status == 200
    assert resp_event.request_id == "rid-99"
    assert resp_event.elapsed_ms is not None and resp_event.elapsed_ms >= 0
    # Token must never appear anywhere in the event payload.
    for e in events:
        for v in vars(e).values():
            assert secret not in str(v), f"api_token leaked into event field: {v!r}"


@respx.mock
def test_on_event_exception_kind_on_connection_error() -> None:
    respx.post("https://api.audd.io/").mock(side_effect=httpx.ConnectError("boom"))
    events: list[AudDEvent] = []
    client = AudD(api_token="t", on_event=events.append)
    with pytest.raises(AudDConnectionError):
        client.recognize("https://x.mp3")
    client.close()
    kinds = [e.kind for e in events]
    assert "exception" in kinds


@respx.mock
def test_on_event_hook_exception_is_swallowed() -> None:
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(200, json={"status": "success", "result": None}),
    )

    def bad_hook(_: AudDEvent) -> None:
        raise RuntimeError("hook exploded")

    client = AudD(api_token="t", on_event=bad_hook)
    # Should NOT raise — hook errors are caught and logged at debug.
    client.recognize("https://x.mp3")
    client.close()
