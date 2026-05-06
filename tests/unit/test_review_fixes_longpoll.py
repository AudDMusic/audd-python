"""Regression tests for LongpollConsumer hardening (S5, S6, M2)."""
from __future__ import annotations

import httpx
import pytest
import respx

from audd import AsyncLongpollConsumer, AudDServerError, LongpollConsumer

# ============================================================================
# S5 — non-2xx HTTP must raise (terminal error on poll.errors), not silently loop forever.
# ============================================================================

@respx.mock
def test_s5_consumer_emits_terminal_error_on_403() -> None:
    respx.get("https://api.audd.io/longpoll/").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"}),
    )
    cons = LongpollConsumer("cat", max_retries=1)
    with cons.iterate(timeout=1) as poll:
        for err in poll.errors:
            assert isinstance(err, AudDServerError)
            assert err.http_status == 403
            break


@respx.mock
def test_s5_consumer_retries_5xx_then_succeeds() -> None:
    """READ-class retries: 5xx is retried, 2xx after retry yields a match."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.port or 0)
        if len(calls) < 2:
            return httpx.Response(503)
        return httpx.Response(200, json={
            "status": "success",
            "result": {
                "radio_id": 1, "timestamp": "x",
                "results": [{"artist": "A", "title": "T", "score": 99}],
            },
            "timestamp": 1,
        })

    respx.get("https://api.audd.io/longpoll/").mock(side_effect=handler)
    cons = LongpollConsumer("cat", backoff_factor=0)
    with cons.iterate(timeout=1) as poll:
        for m in poll.matches:
            assert m.song.artist == "A"
            break
    # 503 → retry → 200; the background poller may have started a 3rd request
    # before we closed, which is fine (and bounded by close()).
    assert len(calls) >= 2


# ============================================================================
# S6 — retry / timeout / transport configurability.
# ============================================================================

@respx.mock
def test_s6_max_retries_zero_emits_error() -> None:
    calls: list[int] = []
    respx.get("https://api.audd.io/longpoll/").mock(
        side_effect=lambda r: (calls.append(0), httpx.Response(503))[1],
    )
    cons = LongpollConsumer("cat", max_retries=1, backoff_factor=0)
    with cons.iterate(timeout=1) as poll:
        for err in poll.errors:
            assert isinstance(err, AudDServerError)
            break
    assert len(calls) == 1


# ============================================================================
# M2 — context-manager protocol on the consumer + on the poll handle.
# ============================================================================

@respx.mock
def test_m2_consumer_as_context_manager() -> None:
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
    with LongpollConsumer("cat") as cons:
        with cons.iterate(timeout=1) as poll:
            for m in poll.matches:
                assert m.song.title == "T"
                break


@pytest.mark.asyncio
@respx.mock
async def test_m2_async_consumer_as_context_manager() -> None:
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
    async with AsyncLongpollConsumer("cat") as cons:
        poll = cons.iterate(timeout=1)
        async with poll:
            async for m in poll.matches:
                assert m.song.title == "T"
                break
