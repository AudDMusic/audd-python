"""Unit tests for the streams namespace."""
from __future__ import annotations

import httpx
import pytest
import respx

from audd import AudD
from audd.errors import AudDAPIError, AudDInvalidRequestError


@respx.mock
def test_set_callback_url_basic() -> None:
    respx.post("https://api.audd.io/setCallbackUrl/").mock(
        return_value=httpx.Response(200, json={"status": "success", "result": None}),
    )
    AudD(api_token="t").streams.set_callback_url("https://x.com/cb")


@respx.mock
def test_set_callback_url_with_return_metadata_appends_query() -> None:
    captured: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req.read().decode())
        return httpx.Response(200, json={"status": "success", "result": None})

    respx.post("https://api.audd.io/setCallbackUrl/").mock(side_effect=handler)
    AudD(api_token="t").streams.set_callback_url(
        "https://x.com/cb",
        return_metadata="apple_music,deezer",
    )
    body = captured[0]
    # The URL with the appended ?return=... is form-encoded inside the body — comma is
    # encoded once for the URL, then again for the form field.
    assert "url=https" in body
    assert "return%3D" in body  # %3D is `=`, the URL's `?return=` after form-encoding


@respx.mock
def test_set_callback_url_raises_on_duplicate_return_param() -> None:
    with pytest.raises(AudDInvalidRequestError) as ei:
        AudD(api_token="t").streams.set_callback_url(
            "https://x.com/cb?return=spotify",
            return_metadata="apple_music",
        )
    assert "already contains a `return`" in str(ei.value)


@respx.mock
def test_get_callback_url_returns_string() -> None:
    respx.post("https://api.audd.io/getCallbackUrl/").mock(
        return_value=httpx.Response(200, json={"status": "success", "result": "https://x.com/cb"}),
    )
    assert AudD(api_token="t").streams.get_callback_url() == "https://x.com/cb"


@respx.mock
def test_add_stream_902_raises_api_error() -> None:
    respx.post("https://api.audd.io/addStream/").mock(
        return_value=httpx.Response(200, json={
            "status": "error",
            "error": {"error_code": 902, "error_message": "limit reached"},
        }),
    )
    with pytest.raises(AudDAPIError) as ei:
        AudD(api_token="t").streams.add("https://stream.url", radio_id=1)
    assert ei.value.error_code == 902


@respx.mock
def test_list_streams_returns_typed_list() -> None:
    respx.post("https://api.audd.io/getStreams/").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "result": [{
                "radio_id": 1, "url": "https://x.com",
                "stream_running": True, "longpoll_category": "abc",
            }],
        }),
    )
    streams = AudD(api_token="t").streams.list()
    assert len(streams) == 1
    assert streams[0].radio_id == 1


@respx.mock
def test_longpoll_preflight_no_callback_raises() -> None:
    """When getCallbackUrl returns code 19, longpoll() raises with helpful message."""
    respx.post("https://api.audd.io/getCallbackUrl/").mock(
        return_value=httpx.Response(200, json={
            "status": "error",
            "error": {"error_code": 19, "error_message": "Internal error"},
        }),
    )
    client = AudD(api_token="t")
    with pytest.raises(AudDInvalidRequestError) as ei:
        next(client.streams.longpoll("cat-abc"))
    msg = str(ei.value)
    assert "no callback URL is configured" in msg
    assert "audd.tech/empty" in msg
    assert "skip_callback_check" in msg


@respx.mock
def test_longpoll_preflight_bypass_with_flag() -> None:
    """skip_callback_check=True skips the preflight; longpoll proceeds."""
    respx.get("https://api.audd.io/longpoll/").mock(
        return_value=httpx.Response(200, json={
            "timeout": "no events before timeout",
            "timestamp": 1234,
        }),
    )
    client = AudD(api_token="t")
    iterator = client.streams.longpoll("cat-abc", skip_callback_check=True, timeout=1)
    payload = next(iterator)
    assert payload["timestamp"] == 1234


@respx.mock
def test_derive_longpoll_category_helper() -> None:
    cat = AudD(api_token="d29ebb205488e3b414bcc0c50432463e").streams.derive_longpoll_category(1)
    assert cat == "088719f57"
