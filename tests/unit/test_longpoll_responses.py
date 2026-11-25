"""Longpoll response handling: events, timeouts, error bodies, odd bodies,
backoff, request timeouts, and shutdown."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any

import httpx
import pytest
import respx

from audd import (
    AsyncAudD,
    AsyncLongpollConsumer,
    AudD,
    AudDConnectionError,
    AudDInvalidRequestError,
    LongpollConsumer,
)
from audd.streams import _IdleBackoff, _LongpollOutcome

LONGPOLL_URL = "https://api.audd.io/longpoll/"
ERROR_BODY = {"error": "Invalid timeout arg.  Must be 1-120."}


def _song_result(artist: str, title: str = "T", radio_id: int = 7) -> dict[str, Any]:
    return {
        "status": "success",
        "result": {
            "radio_id": radio_id,
            "timestamp": "2026-11-24 19:10:11",
            "play_length": 100,
            "results": [{"artist": artist, "title": title, "score": 100}],
        },
    }


def _event(ts: Any, data: Any) -> dict[str, Any]:
    return {"timestamp": ts, "category": "cat", "data": data}


def _serve(bodies: list[Any], captured: list[httpx.Request]) -> Any:
    """Serve ``bodies`` in order (dicts as JSON, str/bytes raw); the last repeats."""
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        body = bodies[min(len(captured), len(bodies)) - 1]
        if isinstance(body, (str, bytes)):
            return httpx.Response(200, content=body)
        return httpx.Response(200, json=body)
    return handler


def _poll(client: AudD, **kwargs: Any) -> Any:
    kwargs.setdefault("timeout", 30)
    return client.streams.longpoll("cat", skip_callback_check=True, **kwargs)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@respx.mock
def test_unparseable_and_empty_events_are_skipped_without_ending_the_poll() -> None:
    empty_results = {"status": "success", "result": {"radio_id": 7, "results": []}}
    body = {"events": [
        _event(10, empty_results),
        _event(11, "not json"),
        _event(12, 42),
        _event(13, None),
        "not an event",
        _event(14, _song_result("Kept")),
    ]}
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve([body, ERROR_BODY], captured))
    with _poll(AudD(api_token="t")) as poll:
        matches = list(poll.matches)
        errors = list(poll.errors)
    assert [m.song.artist for m in matches] == ["Kept"]
    assert captured[1].url.params["since_time"] == "14"
    assert [type(e) for e in errors] == [AudDInvalidRequestError]


@respx.mock
def test_event_data_as_json_string_is_decoded() -> None:
    body = {"events": [_event(5, json.dumps(_song_result("FromString")))]}
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve([body, ERROR_BODY], captured))
    with _poll(AudD(api_token="t")) as poll:
        matches = list(poll.matches)
    assert [m.song.artist for m in matches] == ["FromString"]
    assert matches[0].radio_id == 7


@respx.mock
def test_since_time_is_the_largest_event_timestamp_even_out_of_order() -> None:
    body = {"events": [
        _event(300, _song_result("A")),
        _event("200", _song_result("B")),  # numeric string timestamps are read too
    ]}
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve([body, ERROR_BODY], captured))
    with _poll(AudD(api_token="t")) as poll:
        matches = list(poll.matches)
    assert [m.song.artist for m in matches] == ["A", "B"]
    assert captured[1].url.params["since_time"] == "300"


@respx.mock
def test_events_at_or_before_since_time_are_not_delivered_again() -> None:
    body = {"events": [_event(100, _song_result("Old")), _event(150, _song_result("New"))]}
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve([body, ERROR_BODY], captured))
    with _poll(AudD(api_token="t"), since_time=100) as poll:
        matches = list(poll.matches)
    assert captured[0].url.params["since_time"] == "100"
    assert [m.song.artist for m in matches] == ["New"]
    assert captured[1].url.params["since_time"] == "150"


@respx.mock
def test_events_and_notifications_keep_server_order_per_channel() -> None:
    notif = {"status": "-", "notification": {"radio_id": 7, "notification_code": 650}}
    body = {"events": [
        _event(1, _song_result("First")),
        _event(2, notif),
        _event(3, _song_result("Second")),
    ]}
    respx.get(LONGPOLL_URL).mock(side_effect=_serve([body, ERROR_BODY], []))
    with _poll(AudD(api_token="t")) as poll:
        matches = list(poll.matches)
        notifications = list(poll.notifications)
    assert [m.song.artist for m in matches] == ["First", "Second"]
    assert [n.notification_code for n in notifications] == [650]


# ---------------------------------------------------------------------------
# Old top-level shape
# ---------------------------------------------------------------------------


@respx.mock
def test_top_level_result_shape_still_delivers_and_advances_since_time() -> None:
    body = {**_song_result("TopLevel"), "timestamp": 4242}
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve([body, ERROR_BODY], captured))
    with _poll(AudD(api_token="t")) as poll:
        matches = list(poll.matches)
    assert [m.song.artist for m in matches] == ["TopLevel"]
    assert captured[1].url.params["since_time"] == "4242"


@pytest.mark.asyncio
@respx.mock
async def test_async_top_level_notification_shape_still_delivers() -> None:
    body = {"notification": {"radio_id": 1, "notification_code": 650}, "timestamp": 9}
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve([body, ERROR_BODY], captured))
    async with AsyncAudD(api_token="t") as audd:
        poll = await audd.streams.longpoll("cat", skip_callback_check=True, timeout=30)
        async with poll:
            notifications = [n async for n in poll.notifications]
    assert [n.notification_code for n in notifications] == [650]
    assert captured[1].url.params["since_time"] == "9"


# ---------------------------------------------------------------------------
# Error bodies
# ---------------------------------------------------------------------------


@respx.mock
def test_standard_error_envelope_ends_the_poll_with_a_typed_error() -> None:
    body = {"status": "error", "error": {"error_code": 900, "error_message": "bad token"}}
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve([body], captured))
    with _poll(AudD(api_token="t")) as poll:
        errors = list(poll.errors)
        time.sleep(0.2)
    from audd import AudDAuthenticationError
    assert [type(e) for e in errors] == [AudDAuthenticationError]
    assert len(captured) == 1


@respx.mock
def test_http_403_error_text_is_kept_in_the_message() -> None:
    respx.get(LONGPOLL_URL).mock(
        return_value=httpx.Response(403, json={"error": "forbidden category"}),
    )
    with _poll(AudD(api_token="t")) as poll:
        errors = list(poll.errors)
    assert len(errors) == 1
    assert "403" in str(errors[0])
    assert "forbidden category" in str(errors[0])


# ---------------------------------------------------------------------------
# Bodies that bring nothing: no tight loop, no silent end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("body", [
    "<html>bad gateway</html>",
    "",
    {"unexpected": True},
    {"events": []},
    [1, 2, 3],
])
@respx.mock
def test_body_with_nothing_usable_backs_off_instead_of_spinning(body: Any) -> None:
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve([body], captured))
    poll = _poll(AudD(api_token="t"))
    try:
        time.sleep(0.6)
        assert len(captured) == 1  # the next request waits about a second
        assert poll._thread.is_alive()  # and the subscription hasn't ended
        assert list(poll._errors.queue) == []
        assert "since_time" not in captured[0].url.params
    finally:
        poll.close()
    # close() interrupts the backoff wait.
    poll._thread.join(timeout=1.0)
    assert not poll._thread.is_alive()


@pytest.mark.asyncio
@respx.mock
async def test_async_body_with_nothing_usable_backs_off_instead_of_spinning() -> None:
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve(["not json"], captured))
    async with AsyncAudD(api_token="t") as audd:
        poll = await audd.streams.longpoll("cat", skip_callback_check=True, timeout=30)
        await asyncio.sleep(0.6)
        assert len(captured) == 1
        assert poll._task is not None and not poll._task.done()
        started = time.monotonic()
        await poll.aclose()
        assert time.monotonic() - started < 0.5


@respx.mock
def test_repeated_timeout_with_the_same_timestamp_backs_off() -> None:
    body = {"timeout": "no events before timeout", "timestamp": 500}
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve([body], captured))
    poll = _poll(AudD(api_token="t"))
    try:
        time.sleep(0.6)
        # First response gives a new timestamp (request again at once); the
        # second repeats it, so the third waits.
        assert len(captured) == 2
        assert captured[1].url.params["since_time"] == "500"
    finally:
        poll.close()


@respx.mock
def test_unusable_body_logs_a_warning_without_the_body(caplog: pytest.LogCaptureFixture) -> None:
    respx.get(LONGPOLL_URL).mock(side_effect=_serve(["secret-looking body"], []))
    with caplog.at_level(logging.WARNING, logger="audd"):
        poll = _poll(AudD(api_token="t"))
        time.sleep(0.2)
        poll.close()
    messages = [r.getMessage() for r in caplog.records if r.name == "audd"]
    assert any("no events and no new timestamp" in m for m in messages)
    assert all("secret-looking" not in m for m in messages)


def test_idle_backoff_doubles_to_a_cap_and_resets_on_progress() -> None:
    idle = _IdleBackoff()
    nothing = _LongpollOutcome()
    delays = [idle.delay_after(nothing, None) for _ in range(7)]
    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    assert idle.delay_after(_LongpollOutcome(since=5), None) == 0.0
    assert idle.delay_after(nothing, 5) == 1.0


# ---------------------------------------------------------------------------
# Network failures
# ---------------------------------------------------------------------------


@respx.mock
def test_network_failure_is_retried_then_reaches_the_error_path() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("connection refused")

    respx.get(LONGPOLL_URL).mock(side_effect=handler)
    client = AudD(api_token="t", max_retries=2, backoff_factor=0)
    with _poll(client) as poll:
        errors = list(poll.errors)
    assert [type(e) for e in errors] == [AudDConnectionError]
    assert len(calls) == 2


@respx.mock
def test_5xx_is_retried_and_the_poll_continues() -> None:
    bodies: list[Any] = []
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if len(captured) == 1:
            return httpx.Response(502, text="bad gateway")
        bodies.append(1)
        if len(bodies) == 1:
            return httpx.Response(200, json={"events": [_event(77, _song_result("After5xx"))]})
        return httpx.Response(200, json=ERROR_BODY)

    respx.get(LONGPOLL_URL).mock(side_effect=handler)
    client = AudD(api_token="t", backoff_factor=0)
    with _poll(client) as poll:
        matches = list(poll.matches)
    assert [m.song.artist for m in matches] == ["After5xx"]
    assert captured[2].url.params["since_time"] == "77"


# ---------------------------------------------------------------------------
# No api_token on the request
# ---------------------------------------------------------------------------


@respx.mock
def test_longpoll_requests_never_carry_the_token_after_events_and_errors() -> None:
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve(
        [{"events": [_event(1, _song_result("A"))]}, ERROR_BODY], captured,
    ))
    token = "tok-abcdef-123456"
    with AudD(api_token=token).streams.longpoll(
        radio_id=3, skip_callback_check=True, timeout=30,
    ) as poll:
        errors = list(poll.errors)
    assert len(captured) == 2
    for req in captured:
        assert "api_token" not in req.url.params
        assert token not in str(req.url)
        assert all(token not in v for v in req.headers.values())
    assert token not in str(errors[0])


# ---------------------------------------------------------------------------
# timeout argument
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, 121, 180, -1, 30.5, True, "30"])
def test_timeout_outside_1_to_120_is_rejected(bad: Any) -> None:
    client = AudD(api_token="t")
    with pytest.raises(ValueError, match="1 to 120"):
        client.streams.longpoll("cat", skip_callback_check=True, timeout=bad)
    with pytest.raises(ValueError, match="1 to 120"):
        with LongpollConsumer("cat") as consumer:
            consumer.iterate(timeout=bad)


@pytest.mark.asyncio
async def test_async_timeout_outside_1_to_120_is_rejected() -> None:
    async with AsyncAudD(api_token="t") as audd:
        with pytest.raises(ValueError):
            await audd.streams.longpoll("cat", skip_callback_check=True, timeout=121)
    async with AsyncLongpollConsumer("cat") as consumer:
        with pytest.raises(ValueError):
            consumer.iterate(timeout=0)


def _capture_read_timeout(seen: list[float]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json=ERROR_BODY)
    return handler


@pytest.mark.parametrize("timeout", [1, 50, 120])
@respx.mock
def test_http_read_timeout_outlasts_the_poll(timeout: int) -> None:
    seen: list[float] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_capture_read_timeout(seen))
    with _poll(AudD(api_token="t"), timeout=timeout) as poll:
        list(poll.errors)
    with LongpollConsumer("cat") as consumer, consumer.iterate(timeout=timeout) as poll:
        list(poll.errors)
    assert seen == [timeout + 10.0, timeout + 10.0]


def test_injected_client_gets_a_read_timeout_sized_to_the_poll() -> None:
    seen: list[float] = []
    injected = httpx.Client(transport=httpx.MockTransport(_capture_read_timeout(seen)))
    with _poll(AudD(api_token="t", httpx_client=injected), timeout=50) as poll:
        list(poll.errors)
    injected2 = httpx.Client(transport=httpx.MockTransport(_capture_read_timeout(seen)))
    with LongpollConsumer("cat", httpx_client=injected2) as consumer:
        with consumer.iterate(timeout=50) as poll:
            list(poll.errors)
    assert seen == [60.0, 60.0]


@pytest.mark.asyncio
async def test_async_injected_client_gets_a_read_timeout_sized_to_the_poll() -> None:
    seen: list[float] = []
    injected = httpx.AsyncClient(transport=httpx.MockTransport(_capture_read_timeout(seen)))
    async with AsyncAudD(api_token="t", httpx_client=injected) as audd:
        poll = await audd.streams.longpoll("cat", skip_callback_check=True, timeout=90)
        async with poll:
            _ = [e async for e in poll.errors]
    assert seen == [100.0]


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


@respx.mock
def test_close_ends_iteration_and_drops_the_response_of_a_waiting_request() -> None:
    release = threading.Event()
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        release.wait(5)
        return httpx.Response(200, json={"events": [_event(1, _song_result("Late"))]})

    respx.get(LONGPOLL_URL).mock(side_effect=handler)
    poll = _poll(AudD(api_token="t"))
    deadline = time.monotonic() + 2
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    got: list[Any] = []
    reader = threading.Thread(target=lambda: got.extend(poll.matches))
    reader.start()
    started = time.monotonic()
    poll.close()
    reader.join(timeout=2)
    assert not reader.is_alive()  # iteration ended on close()
    assert time.monotonic() - started < 1.5
    release.set()
    poll._thread.join(timeout=2)
    assert not poll._thread.is_alive()
    assert got == []
    assert poll._matches.empty()  # the late response was not delivered
    assert len(calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_async_aclose_cancels_a_waiting_request() -> None:
    started_request = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        started_request.set()
        await asyncio.sleep(30)
        return httpx.Response(200, json=ERROR_BODY)

    respx.get(LONGPOLL_URL).mock(side_effect=handler)
    async with AsyncAudD(api_token="t") as audd:
        poll = await audd.streams.longpoll("cat", skip_callback_check=True, timeout=30)
        await asyncio.wait_for(started_request.wait(), 2)
        t0 = time.monotonic()
        await poll.aclose()
        assert time.monotonic() - t0 < 0.5
        assert poll._task is not None and poll._task.done()
        assert [m async for m in poll.matches] == []
