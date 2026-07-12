"""Transport-layer regressions: tokenless longpoll GETs, async longpoll
liveness under undrained notifications, injected-client enterprise timeouts,
and path-source file-handle cleanup."""
from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from audd import AsyncAudD, AudD
from audd._http import ENTERPRISE_TIMEOUTS
from audd._source import prepare_source

LONGPOLL_URL = "https://api.audd.io/longpoll/"

_MATCH_BODY = {
    "status": "success",
    "result": {
        "radio_id": 1, "timestamp": "x",
        "results": [{"artist": "A", "title": "T", "score": 99}],
    },
    "timestamp": 1,
}


# ============================================================================
# Longpoll GETs must not carry the api_token (category alone authorizes).
# ============================================================================


@respx.mock
def test_longpoll_get_does_not_send_api_token() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200, json=_MATCH_BODY)

    respx.get(LONGPOLL_URL).mock(side_effect=handler)
    client = AudD(api_token="secret-token-12345")
    with client.streams.longpoll(
        radio_id=1, skip_callback_check=True, timeout=1,
    ) as poll:
        for _m in poll.matches:
            break
    assert captured
    for req in captured:
        assert "api_token" not in req.url.params
        assert "secret-token-12345" not in str(req.url)
    # The derived category is still sent.
    assert captured[0].url.params["category"]


@pytest.mark.asyncio
@respx.mock
async def test_async_longpoll_get_does_not_send_api_token() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200, json=_MATCH_BODY)

    respx.get(LONGPOLL_URL).mock(side_effect=handler)
    async with AsyncAudD(api_token="secret-token-12345") as audd:
        poll = await audd.streams.longpoll(
            radio_id=1, skip_callback_check=True, timeout=1,
        )
        async with poll:
            async for _m in poll.matches:
                break
    assert captured
    for req in captured:
        assert "api_token" not in req.url.params
        assert "secret-token-12345" not in str(req.url)


# ============================================================================
# Async longpoll must keep polling even when nobody drains notifications.
# ============================================================================


@pytest.mark.asyncio
@respx.mock
async def test_async_longpoll_not_stalled_by_undrained_notifications() -> None:
    """Undrained lifecycle notifications must never stall the poll loop."""
    calls: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={
            "notification": {"radio_id": 1, "stream_running": False,
                             "notification_message": "stream stopped"},
            "timestamp": len(calls),
        })

    respx.get(LONGPOLL_URL).mock(side_effect=handler)
    async with AsyncAudD(api_token="t") as audd:
        poll = await audd.streams.longpoll(
            category="cat-abc", skip_callback_check=True, timeout=1,
        )
        async with poll:
            # Consume nothing. The poller must still cycle through multiple
            # notifications (with bounded queues it froze after two).
            deadline = asyncio.get_running_loop().time() + 5.0
            while len(calls) < 4:
                assert asyncio.get_running_loop().time() < deadline, (
                    f"poll loop stalled after {len(calls)} notifications"
                )
                await asyncio.sleep(0.01)
            # All queued notifications are still delivered on drain.
            drained = 0
            async for _n in poll.notifications:
                drained += 1
                if drained >= 4:
                    break
    assert len(calls) >= 4


# ============================================================================
# Injected httpx clients get the enterprise 1-hour read timeout per-request.
# ============================================================================


def _enterprise_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": "success", "result": []})


def test_injected_client_gets_enterprise_timeout() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return _enterprise_ok(request)

    injected = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    client = AudD(api_token="t", httpx_client=injected)
    client.recognize_enterprise("https://example.mp3", limit=1)
    assert seen["timeout"] is not None
    assert seen["timeout"]["read"] == ENTERPRISE_TIMEOUTS.read == 3600.0


@pytest.mark.asyncio
async def test_async_injected_client_gets_enterprise_timeout() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return _enterprise_ok(request)

    injected = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    async with AsyncAudD(api_token="t", httpx_client=injected) as audd:
        await audd.recognize_enterprise("https://example.mp3", limit=1)
    assert seen["timeout"] is not None
    assert seen["timeout"]["read"] == 3600.0


def test_injected_client_keeps_own_timeout_on_standard_endpoint() -> None:
    """The standard endpoint must not override an injected client's timeouts."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={"status": "success", "result": None})

    injected = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    client = AudD(api_token="t", httpx_client=injected)
    client.recognize("https://example.mp3")
    assert seen["timeout"]["read"] == 5.0


# ============================================================================
# Path sources: the SDK closes the handles it opens.
# ============================================================================


def test_prepare_source_path_provides_working_cleanup(tmp_path: Path) -> None:
    f = tmp_path / "song.mp3"
    f.write_bytes(b"\x00\x01")
    _data, files, cleanup = prepare_source(f)()
    assert files is not None and cleanup is not None
    handle = files["file"][1]
    assert not handle.closed
    cleanup()
    assert handle.closed


def test_prepare_source_never_closes_caller_file_likes() -> None:
    buf = io.BytesIO(b"\x00\x01")
    _data, _files, cleanup = prepare_source(buf)()
    assert cleanup is None


@respx.mock
def test_recognize_closes_path_file_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = tmp_path / "song.mp3"
    f.write_bytes(b"\x00" * 16)
    opened: list[Any] = []
    orig_open = Path.open

    def spy(self: Path, *args: Any, **kwargs: Any) -> Any:
        handle = orig_open(self, *args, **kwargs)
        if self == f:
            opened.append(handle)
        return handle

    monkeypatch.setattr(Path, "open", spy)
    respx.post("https://api.audd.io/").mock(
        return_value=httpx.Response(200, json={"status": "success", "result": None}),
    )
    AudD(api_token="t").recognize(f)
    assert opened
    assert all(h.closed for h in opened)


@respx.mock
def test_recognize_closes_path_file_handle_on_http_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = tmp_path / "song.mp3"
    f.write_bytes(b"\x00" * 16)
    opened: list[Any] = []
    orig_open = Path.open

    def spy(self: Path, *args: Any, **kwargs: Any) -> Any:
        handle = orig_open(self, *args, **kwargs)
        if self == f:
            opened.append(handle)
        return handle

    monkeypatch.setattr(Path, "open", spy)
    respx.post("https://api.audd.io/").mock(side_effect=httpx.ConnectError("boom"))
    from audd import AudDConnectionError

    with pytest.raises(AudDConnectionError):
        AudD(api_token="t", max_retries=1).recognize(f)
    assert opened
    assert all(h.closed for h in opened)
