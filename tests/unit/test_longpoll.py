"""Unit tests for tokenless LongpollConsumer."""
from __future__ import annotations

import httpx
import pytest
import respx

from audd import AsyncLongpollConsumer, LongpollConsumer


@respx.mock
def test_consumer_dispatches_match() -> None:
    respx.get("https://api.audd.io/longpoll/").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "result": {
                "radio_id": 7, "timestamp": "x",
                "results": [{"artist": "A", "title": "T", "score": 99}],
            },
            "timestamp": 100,
        }),
    )
    cons = LongpollConsumer("cat-abc")
    with cons.iterate(timeout=1) as poll:
        for m in poll.matches:
            assert m.song.artist == "A"
            break


@respx.mock
def test_consumer_does_not_send_api_token() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200, json={
            "status": "success",
            "result": {
                "radio_id": 1, "timestamp": "x",
                "results": [{"artist": "A", "title": "T", "score": 99}],
            },
            "timestamp": 1,
        })

    respx.get("https://api.audd.io/longpoll/").mock(side_effect=handler)
    cons = LongpollConsumer("cat-abc")
    with cons.iterate(timeout=1) as poll:
        for _ in poll.matches:
            break
    sent = captured[0]
    qs = sent.url.params
    assert "api_token" not in qs
    assert qs["category"] == "cat-abc"


@respx.mock
def test_consumer_advances_since_time_across_iterations() -> None:
    """The second longpoll must carry since_time from the first response's timestamp."""
    captured: list[httpx.Request] = []
    counter = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        counter["n"] += 1
        if counter["n"] == 1:
            return httpx.Response(200, json={
                "timeout": "no events before timeout", "timestamp": 1001,
            })
        return httpx.Response(200, json={
            "status": "success",
            "result": {
                "radio_id": 1, "timestamp": "x",
                "results": [{"artist": "A", "title": "T", "score": 99}],
            },
            "timestamp": 1002,
        })

    respx.get("https://api.audd.io/longpoll/").mock(side_effect=handler)
    cons = LongpollConsumer("cat-abc")
    with cons.iterate(timeout=1) as poll:
        for _ in poll.matches:
            break
    assert "since_time" not in captured[0].url.params
    assert captured[1].url.params["since_time"] == "1001"


@pytest.mark.asyncio
@respx.mock
async def test_async_consumer() -> None:
    respx.get("https://api.audd.io/longpoll/").mock(
        return_value=httpx.Response(200, json={
            "status": "success",
            "result": {
                "radio_id": 1, "timestamp": "x",
                "results": [{"artist": "A", "title": "T", "score": 99}],
            },
            "timestamp": 1,
        }),
    )
    async with AsyncLongpollConsumer("cat-abc") as cons:
        poll = cons.iterate(timeout=1)
        async with poll:
            async for m in poll.matches:
                assert m.song.artist == "A"
                break
