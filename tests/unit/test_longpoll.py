"""Unit tests for tokenless LongpollConsumer."""
from __future__ import annotations

import httpx
import pytest
import respx

from audd import AsyncLongpollConsumer, LongpollConsumer


@respx.mock
def test_consumer_iterates_yielding_responses() -> None:
    respx.get("https://api.audd.io/longpoll/").mock(
        return_value=httpx.Response(200, json={
            "timeout": "no events before timeout", "timestamp": 100,
        }),
    )
    cons = LongpollConsumer("cat-abc")
    iterator = cons.iterate(timeout=1)
    payload = next(iterator)
    assert payload == {"timeout": "no events before timeout", "timestamp": 100}


@respx.mock
def test_consumer_does_not_send_api_token_header() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200, json={
            "timeout": "no events before timeout", "timestamp": 1,
        })

    respx.get("https://api.audd.io/longpoll/").mock(side_effect=handler)
    cons = LongpollConsumer("cat-abc")
    next(cons.iterate(timeout=1))
    sent = captured[0]
    qs = sent.url.params
    assert "api_token" not in qs
    assert qs["category"] == "cat-abc"


@respx.mock
def test_consumer_advances_since_time_across_iterations() -> None:
    """When the response includes timestamp, the next request should send it as since_time."""
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200, json={
            "timeout": "no events before timeout",
            "timestamp": 1000 + len(captured),
        })

    respx.get("https://api.audd.io/longpoll/").mock(side_effect=handler)
    cons = LongpollConsumer("cat-abc")
    it = cons.iterate(timeout=1)
    next(it)
    next(it)
    next(it)
    assert "since_time" not in captured[0].url.params
    assert captured[1].url.params["since_time"] == "1001"
    assert captured[2].url.params["since_time"] == "1002"


@pytest.mark.asyncio
@respx.mock
async def test_async_consumer() -> None:
    respx.get("https://api.audd.io/longpoll/").mock(
        return_value=httpx.Response(200, json={
            "timeout": "no events before timeout", "timestamp": 1,
        }),
    )
    cons = AsyncLongpollConsumer("cat-abc")
    async for payload in cons.iterate(timeout=1):
        assert payload == {"timeout": "no events before timeout", "timestamp": 1}
        break
    await cons.aclose()
