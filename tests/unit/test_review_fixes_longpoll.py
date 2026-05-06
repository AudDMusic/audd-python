"""Regression tests for LongpollConsumer hardening (S5, S6, M2)."""
from __future__ import annotations

import httpx
import pytest
import respx

from audd import AsyncLongpollConsumer, AudDServerError, LongpollConsumer

# ============================================================================
# S5 — non-2xx HTTP must raise, not silently loop forever.
# ============================================================================

@respx.mock
def test_s5_consumer_raises_on_403() -> None:
    respx.get("https://api.audd.io/longpoll/").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"}),
    )
    cons = LongpollConsumer("cat", max_retries=1)
    with pytest.raises(AudDServerError) as ei:
        next(cons.iterate(timeout=1))
    assert ei.value.http_status == 403


@respx.mock
def test_s5_consumer_retries_5xx_then_succeeds() -> None:
    """READ-class retries: 5xx is retried, 2xx after retry returns."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.port or 0)
        if len(calls) < 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"timeout": "no events before timeout", "timestamp": 1})

    respx.get("https://api.audd.io/longpoll/").mock(side_effect=handler)
    cons = LongpollConsumer("cat", backoff_factor=0)
    payload = next(cons.iterate(timeout=1))
    assert len(calls) == 2
    assert payload["timestamp"] == 1


# ============================================================================
# S6 — retry / timeout / transport configurability.
# ============================================================================

@respx.mock
def test_s6_max_retries_zero_does_not_retry() -> None:
    calls: list[int] = []
    respx.get("https://api.audd.io/longpoll/").mock(
        side_effect=lambda r: (calls.append(0), httpx.Response(503))[1],
    )
    cons = LongpollConsumer("cat", max_retries=1, backoff_factor=0)
    with pytest.raises(AudDServerError):
        next(cons.iterate(timeout=1))
    assert len(calls) == 1


# ============================================================================
# M2 — context-manager protocol.
# ============================================================================

@respx.mock
def test_m2_consumer_as_context_manager() -> None:
    respx.get("https://api.audd.io/longpoll/").mock(
        return_value=httpx.Response(200, json={"timeout": "no events before timeout", "timestamp": 1}),
    )
    with LongpollConsumer("cat") as cons:
        payload = next(cons.iterate(timeout=1))
        assert payload["timestamp"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_m2_async_consumer_as_context_manager() -> None:
    respx.get("https://api.audd.io/longpoll/").mock(
        return_value=httpx.Response(200, json={"timeout": "no events before timeout", "timestamp": 1}),
    )
    async with AsyncLongpollConsumer("cat") as cons:
        async for payload in cons.iterate(timeout=1):
            assert payload["timestamp"] == 1
            break
