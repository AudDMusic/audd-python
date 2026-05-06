"""Unit tests for the streams namespace."""
from __future__ import annotations

import asyncio

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
        client.streams.longpoll("cat-abc")
    msg = str(ei.value)
    assert "no callback URL is configured" in msg
    assert "audd.tech/empty" in msg
    assert "skip_callback_check" in msg


@respx.mock
def test_longpoll_preflight_bypass_with_flag_dispatches_match() -> None:
    """skip_callback_check=True skips the preflight; longpoll proceeds."""
    respx.get("https://api.audd.io/longpoll/").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "result": {
                "radio_id": 7, "timestamp": "x",
                "results": [{"artist": "A", "title": "T", "score": 99}],
            },
            "timestamp": 1234,
        }),
    )
    client = AudD(api_token="t")
    with client.streams.longpoll("cat-abc", skip_callback_check=True, timeout=1) as poll:
        for m in poll.matches:
            assert m.song.artist == "A"
            break


@respx.mock
def test_longpoll_dispatches_notification() -> None:
    respx.get("https://api.audd.io/longpoll/").mock(
        return_value=httpx.Response(200, json={
            "status": "-",
            "notification": {
                "radio_id": 3, "stream_running": False,
                "notification_code": 650, "notification_message": "can't connect",
            },
            "time": 1587939136,
            "timestamp": 1587939136,
        }),
    )
    client = AudD(api_token="t")
    with client.streams.longpoll("cat-abc", skip_callback_check=True, timeout=1) as poll:
        for n in poll.notifications:
            assert n.notification_code == 650
            break


@respx.mock
def test_longpoll_skips_pure_timeout_payload_no_error() -> None:
    """A response that has neither result nor notification (just `timeout`/`timestamp`)
    is *not* a terminal error — it just advances since_time and continues."""
    captured: list[httpx.Request] = []
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        counter["n"] += 1
        # First response: empty timeout payload. Second: a real match.
        if counter["n"] == 1:
            return httpx.Response(200, json={
                "timeout": "no events before timeout", "timestamp": 100,
            })
        return httpx.Response(200, json={
            "status": "success",
            "result": {
                "radio_id": 7, "timestamp": "x",
                "results": [{"artist": "A", "title": "T", "score": 99}],
            },
            "timestamp": 200,
        })

    respx.get("https://api.audd.io/longpoll/").mock(side_effect=handler)
    client = AudD(api_token="t")
    with client.streams.longpoll("cat-abc", skip_callback_check=True, timeout=1) as poll:
        for m in poll.matches:
            assert m.song.artist == "A"
            break
    # since_time threading: the second request must have carried since_time=100.
    assert captured[1].url.params["since_time"] == "100"


@respx.mock
def test_derive_longpoll_category_helper() -> None:
    cat = AudD(api_token="d29ebb205488e3b414bcc0c50432463e").streams.derive_longpoll_category(1)
    assert cat == "088719f57"


# ---------------------------------------------------------------------------
# handle_callback — duck-typed across frameworks.
# ---------------------------------------------------------------------------

class _FakeFlaskRequest:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def get_data(self) -> bytes:
        return self._body


class _FakeDjangoRequest:
    def __init__(self, body: bytes) -> None:
        self.body = body


def test_handle_callback_flask_style() -> None:
    """Flask-style request with .get_data() returning bytes."""
    body = b'{"status":"success","result":{"radio_id":7,"results":[{"artist":"A","title":"T","score":99}]}}'
    match, notif = AudD(api_token="t").streams.handle_callback(_FakeFlaskRequest(body))
    assert notif is None
    assert match is not None
    assert match.radio_id == 7
    assert match.song.artist == "A"


def test_handle_callback_django_style() -> None:
    """Django-style request with .body bytes attribute."""
    body = b'{"status":"-","notification":{"radio_id":3,"stream_running":false,"notification_code":650,"notification_message":"x"},"time":1587939136}'
    match, notif = AudD(api_token="t").streams.handle_callback(_FakeDjangoRequest(body))
    assert match is None
    assert notif is not None
    assert notif.notification_code == 650
    assert notif.time == 1587939136


def test_handle_callback_accepts_raw_bytes() -> None:
    body = b'{"status":"success","result":{"radio_id":7,"results":[{"artist":"A","title":"T","score":99}]}}'
    match, _ = AudD(api_token="t").streams.handle_callback(body)
    assert match is not None
    assert match.song.title == "T"


# ---------------------------------------------------------------------------
# AsyncStreams.handle_callback
# ---------------------------------------------------------------------------

class _FakeFastAPIRequest:
    """Mimics Starlette/FastAPI: ``await request.body()`` returns bytes."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def body(self) -> bytes:
        return self._body


class _FakeAiohttpRequest:
    """Mimics aiohttp: ``await request.read()`` returns bytes."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def read(self) -> bytes:
        return self._body


@pytest.mark.asyncio
async def test_handle_callback_async_fastapi_style() -> None:
    from audd import AsyncAudD

    body = b'{"status":"success","result":{"radio_id":7,"results":[{"artist":"A","title":"T","score":99}]}}'
    async with AsyncAudD(api_token="t") as audd:
        match, notif = await audd.streams.handle_callback(_FakeFastAPIRequest(body))
    assert notif is None
    assert match is not None
    assert match.song.artist == "A"


@pytest.mark.asyncio
async def test_handle_callback_async_aiohttp_style() -> None:
    from audd import AsyncAudD

    body = b'{"status":"success","result":{"radio_id":7,"results":[{"artist":"A","title":"T","score":99}]}}'
    async with AsyncAudD(api_token="t") as audd:
        match, _ = await audd.streams.handle_callback(_FakeAiohttpRequest(body))
    assert match is not None


@pytest.mark.asyncio
@respx.mock
async def test_async_longpoll_dispatches_match() -> None:
    from audd import AsyncAudD

    respx.get("https://api.audd.io/longpoll/").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "result": {
                "radio_id": 7, "timestamp": "x",
                "results": [{"artist": "A", "title": "T", "score": 99}],
            },
            "timestamp": 1234,
        }),
    )
    async with AsyncAudD(api_token="t") as audd:
        poll = await audd.streams.longpoll("cat-abc", skip_callback_check=True, timeout=1)
        try:
            async for m in poll.matches:
                assert m.song.artist == "A"
                break
        finally:
            await poll.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_async_longpoll_as_async_context_manager() -> None:
    from audd import AsyncAudD

    respx.get("https://api.audd.io/longpoll/").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "result": {
                "radio_id": 7, "timestamp": "x",
                "results": [{"artist": "Z", "title": "Q", "score": 90}],
            },
            "timestamp": 1234,
        }),
    )
    async with AsyncAudD(api_token="t") as audd:
        poll = await audd.streams.longpoll("cat-abc", skip_callback_check=True, timeout=1)
        async with poll:
            async for m in poll.matches:
                assert m.song.artist == "Z"
                break


@pytest.mark.asyncio
@respx.mock
async def test_async_longpoll_errors_on_403() -> None:
    """Single terminal error on the errors iterator on HTTP 4xx."""
    from audd import AsyncAudD

    respx.get("https://api.audd.io/longpoll/").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"}),
    )
    async with AsyncAudD(api_token="t") as audd:
        poll = await audd.streams.longpoll(
            "cat-abc", skip_callback_check=True, timeout=1,
        )
        async with poll:
            async for err in poll.errors:
                assert "403" in str(err) or "Longpoll" in str(err)
                break


@pytest.mark.asyncio
@respx.mock
async def test_async_longpoll_concurrent_matches_and_notifications() -> None:
    """Both iterators can be consumed concurrently with asyncio.gather."""
    from audd import AsyncAudD

    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        if counter["n"] == 1:
            return httpx.Response(200, json={
                "status": "success",
                "result": {
                    "radio_id": 7, "timestamp": "x",
                    "results": [{"artist": "A", "title": "T", "score": 99}],
                },
                "timestamp": 100,
            })
        return httpx.Response(200, json={
            "status": "-",
            "notification": {
                "radio_id": 3, "stream_running": False,
                "notification_code": 650, "notification_message": "x",
            },
            "time": 200, "timestamp": 200,
        })

    respx.get("https://api.audd.io/longpoll/").mock(side_effect=handler)
    async with AsyncAudD(api_token="t") as audd:
        poll = await audd.streams.longpoll("c", skip_callback_check=True, timeout=1)

        got_match: list[str] = []
        got_notif: list[int] = []

        async def consume_matches() -> None:
            async for m in poll.matches:
                got_match.append(m.song.artist)
                return

        async def consume_notifs() -> None:
            async for n in poll.notifications:
                got_notif.append(n.notification_code)
                return

        async with poll:
            await asyncio.wait_for(
                asyncio.gather(consume_matches(), consume_notifs()),
                timeout=5.0,
            )
        assert got_match == ["A"]
        assert got_notif == [650]
