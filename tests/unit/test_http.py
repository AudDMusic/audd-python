"""Unit tests for the HTTP transport layer."""
from __future__ import annotations

import httpx
import pytest
import respx

from audd._http import HTTPClient


@respx.mock
def test_post_form_sends_api_token_in_body() -> None:
    route = respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(200, json={"status": "success", "result": None}),
    )
    client = HTTPClient(api_token="t-test")
    resp = client.post_form("https://api.audd.io/", data={"url": "https://example.mp3"})

    assert resp.json_body == {"status": "success", "result": None}
    assert resp.http_status == 200
    sent = route.calls.last.request
    body = sent.read().decode()
    assert "api_token=t-test" in body
    assert "url=https" in body


@respx.mock
def test_response_carries_request_id_when_header_present() -> None:
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success"},
            headers={"x-request-id": "req-123"},
        ),
    )
    client = HTTPClient(api_token="t-test")
    resp = client.post_form("https://api.audd.io/", data={})
    assert resp.request_id == "req-123"


@respx.mock
def test_response_request_id_none_when_header_missing() -> None:
    respx.post("https://api.audd.io/").mock(return_value=httpx.Response(200, json={}))
    client = HTTPClient(api_token="t-test")
    resp = client.post_form("https://api.audd.io/", data={})
    assert resp.request_id is None


@respx.mock
def test_user_agent_set() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json={})

    respx.post("https://api.audd.io/").mock(side_effect=handler)
    HTTPClient(api_token="t").post_form("https://api.audd.io/", data={})
    assert captured["ua"].startswith("audd-python/")


@respx.mock
def test_custom_httpx_client_injection() -> None:
    """Users can inject their own httpx.Client (for proxies, mTLS, custom CA)."""
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
    custom = httpx.Client(transport=transport)
    client = HTTPClient(api_token="t", httpx_client=custom)
    resp = client.post_form("https://api.audd.io/", data={})
    assert resp.json_body == {"ok": True}


@pytest.mark.asyncio
@respx.mock
async def test_async_post_form() -> None:
    respx.post("https://api.audd.io/").mock(return_value=httpx.Response(200, json={"async": True}))
    from audd._http import AsyncHTTPClient

    client = AsyncHTTPClient(api_token="t")
    resp = await client.post_form("https://api.audd.io/", data={})
    assert resp.json_body == {"async": True}
    await client.aclose()
