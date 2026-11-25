"""Longpoll consumers against the audd-openapi longpoll fixtures.

Each test serves fixture bodies from a mocked /longpoll/ endpoint and checks
what reaches the poll handle and what the following request asks for.
"""
from __future__ import annotations

import asyncio
import copy
import json
import time
from typing import Any

import httpx
import pytest
import respx

from audd import (
    AsyncAudD,
    AsyncLongpollConsumer,
    AudD,
    AudDInvalidRequestError,
    LongpollConsumer,
)

LONGPOLL_URL = "https://api.audd.io/longpoll/"
_TOKEN = "secret-token-0123456789"
_ERROR_TEXT = "Invalid timeout arg.  Must be 1-120."


def _serve(bodies: list[Any], captured: list[httpx.Request]) -> Any:
    """Serve ``bodies`` in order; the last one repeats."""
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        body = bodies[min(len(captured), len(bodies)) - 1]
        return httpx.Response(200, json=body)
    return handler


def _drain_sync(poll: Any) -> tuple[list[Any], list[Any], list[Exception]]:
    """Read everything the poll produces until it ends on its own."""
    matches = list(poll.matches)
    notifications = list(poll.notifications)
    errors = list(poll.errors)
    return matches, notifications, errors


async def _drain_async(poll: Any) -> tuple[list[Any], list[Any], list[Exception]]:
    matches = [m async for m in poll.matches]
    notifications = [n async for n in poll.notifications]
    errors = [e async for e in poll.errors]
    return matches, notifications, errors


def _assert_two_songs(matches: list[Any]) -> None:
    assert [(m.song.artist, m.song.title) for m in matches] == [
        ("Alan Walker, A$AP Rocky", "Live Fast (PUBGM)"),
        ("Tears For Fears", "Everybody Wants To Rule The World"),
    ]
    assert [m.radio_id for m in matches] == [7, 7]
    assert [m.play_length for m in matches] == [111, 96]


def _assert_no_token(captured: list[httpx.Request]) -> None:
    for req in captured:
        assert "api_token" not in req.url.params
        assert _TOKEN not in str(req.url)
        assert all(_TOKEN not in v for v in req.headers.values())


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@respx.mock
def test_events_fixture_delivers_both_songs_then_advances_since_time(load_fixture) -> None:
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve(
        [load_fixture("longpoll_events.json"), load_fixture("longpoll_error.json")], captured,
    ))
    client = AudD(api_token=_TOKEN)
    with client.streams.longpoll(radio_id=7, skip_callback_check=True, timeout=30) as poll:
        matches, notifications, errors = _drain_sync(poll)

    _assert_two_songs(matches)
    assert notifications == []
    assert len(captured) == 2
    assert "since_time" not in captured[0].url.params
    assert captured[1].url.params["since_time"] == "1795547548112"
    assert len(errors) == 1
    _assert_no_token(captured)


@pytest.mark.asyncio
@respx.mock
async def test_async_events_fixture_delivers_both_songs_then_advances_since_time(
    load_fixture,
) -> None:
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve(
        [load_fixture("longpoll_events.json"), load_fixture("longpoll_error.json")], captured,
    ))
    async with AsyncAudD(api_token=_TOKEN) as audd:
        poll = await audd.streams.longpoll(radio_id=7, skip_callback_check=True, timeout=30)
        async with poll:
            matches, notifications, errors = await asyncio.wait_for(_drain_async(poll), 5)

    _assert_two_songs(matches)
    assert notifications == []
    assert len(captured) == 2
    assert captured[1].url.params["since_time"] == "1795547548112"
    assert len(errors) == 1
    _assert_no_token(captured)


@respx.mock
def test_tokenless_consumer_reads_events_fixture(load_fixture) -> None:
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve(
        [load_fixture("longpoll_events.json"), load_fixture("longpoll_error.json")], captured,
    ))
    with LongpollConsumer("92b1cc7f0") as consumer, consumer.iterate(timeout=30) as poll:
        matches, _notifications, _errors = _drain_sync(poll)

    _assert_two_songs(matches)
    assert captured[0].url.params["category"] == "92b1cc7f0"
    assert captured[1].url.params["since_time"] == "1795547548112"


@pytest.mark.asyncio
@respx.mock
async def test_async_tokenless_consumer_reads_events_fixture(load_fixture) -> None:
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve(
        [load_fixture("longpoll_events.json"), load_fixture("longpoll_error.json")], captured,
    ))
    async with AsyncLongpollConsumer("92b1cc7f0") as consumer:
        async with consumer.iterate(timeout=30) as poll:
            matches, _notifications, _errors = await asyncio.wait_for(_drain_async(poll), 5)

    _assert_two_songs(matches)
    assert captured[1].url.params["since_time"] == "1795547548112"


@respx.mock
def test_notification_and_json_string_event_data(load_fixture) -> None:
    """A notification event goes to ``notifications``; ``data`` sent as a JSON
    string is decoded first."""
    events = load_fixture("longpoll_events.json")
    first, second = events["events"]
    notification = {
        "timestamp": 1795547500000,
        "category": "92b1cc7f0",
        "data": load_fixture("streams_callback_with_notification.json"),
    }
    second = copy.deepcopy(second)
    second["data"] = json.dumps(second["data"])
    body = {"events": [first, notification, second]}

    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve(
        [body, load_fixture("longpoll_error.json")], captured,
    ))
    client = AudD(api_token=_TOKEN)
    with client.streams.longpoll("92b1cc7f0", skip_callback_check=True, timeout=30) as poll:
        matches, notifications, _errors = _drain_sync(poll)

    _assert_two_songs(matches)
    assert [n.notification_code for n in notifications] == [650]
    assert captured[1].url.params["since_time"] == "1795547548112"


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


@respx.mock
def test_timeout_fixture_delivers_nothing_and_passes_its_timestamp(load_fixture) -> None:
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve(
        [load_fixture("longpoll_no_events.json"), load_fixture("longpoll_error.json")], captured,
    ))
    client = AudD(api_token=_TOKEN)
    with client.streams.longpoll("92b1cc7f0", skip_callback_check=True, timeout=30) as poll:
        matches, notifications, _errors = _drain_sync(poll)

    assert matches == []
    assert notifications == []
    assert len(captured) == 2
    assert captured[1].url.params["since_time"] == "1777901270049"


@pytest.mark.asyncio
@respx.mock
async def test_async_timeout_fixture_passes_its_timestamp(load_fixture) -> None:
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve(
        [load_fixture("longpoll_no_events.json"), load_fixture("longpoll_error.json")], captured,
    ))
    async with AsyncAudD(api_token=_TOKEN) as audd:
        poll = await audd.streams.longpoll("92b1cc7f0", skip_callback_check=True, timeout=30)
        async with poll:
            matches, notifications, _errors = await asyncio.wait_for(_drain_async(poll), 5)

    assert matches == []
    assert notifications == []
    assert captured[1].url.params["since_time"] == "1777901270049"


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


@respx.mock
def test_error_fixture_ends_the_poll_after_one_request(load_fixture) -> None:
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve([load_fixture("longpoll_error.json")], captured))
    client = AudD(api_token=_TOKEN)
    with client.streams.longpoll("92b1cc7f0", skip_callback_check=True, timeout=30) as poll:
        errors = list(poll.errors)
        assert list(poll.matches) == []
        time.sleep(0.3)  # a looping poller would send more requests in this time

    assert len(errors) == 1
    err = errors[0]
    assert isinstance(err, AudDInvalidRequestError)
    assert err.message == _ERROR_TEXT
    assert err.http_status == 200
    assert len(captured) == 1


@pytest.mark.asyncio
@respx.mock
async def test_async_error_fixture_ends_the_poll_after_one_request(load_fixture) -> None:
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve([load_fixture("longpoll_error.json")], captured))
    async with AsyncAudD(api_token=_TOKEN) as audd:
        poll = await audd.streams.longpoll("92b1cc7f0", skip_callback_check=True, timeout=30)
        async with poll:
            _matches, _notifications, errors = await asyncio.wait_for(_drain_async(poll), 5)
            await asyncio.sleep(0.3)

    assert len(errors) == 1
    assert isinstance(errors[0], AudDInvalidRequestError)
    assert errors[0].message == _ERROR_TEXT
    assert len(captured) == 1


@respx.mock
def test_tokenless_consumer_error_fixture_one_request(load_fixture) -> None:
    captured: list[httpx.Request] = []
    respx.get(LONGPOLL_URL).mock(side_effect=_serve([load_fixture("longpoll_error.json")], captured))
    with LongpollConsumer("92b1cc7f0") as consumer, consumer.iterate(timeout=30) as poll:
        errors = list(poll.errors)
        time.sleep(0.3)
    assert [type(e) for e in errors] == [AudDInvalidRequestError]
    assert len(captured) == 1
