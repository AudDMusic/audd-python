"""Tokenless longpoll consumer for browser/widget/extension use cases.

Carries no api_token. The category alone authorizes the subscription. The
user/server who derived the category is responsible for ensuring a callback
URL is set on their account (we can't preflight that without a token).

The consumer's ``iterate()`` returns the same poll-handle shape as
:py:meth:`audd.streams.Streams.longpoll` — three iterators (matches,
notifications, errors) and async/sync context-manager support.
"""
from __future__ import annotations

import httpx

from audd._http import HTTPResponse
from audd._retry import RetryClass, RetryPolicy, retry_async, retry_sync
from audd._user_agent import user_agent
from audd.streams import (
    LONGPOLL_URL,
    _AsyncLongpollPoll,
    _run_longpoll_async,
    _SyncLongpollPoll,
)


class LongpollConsumer:
    """Sync tokenless longpoll consumer.

    .. code-block:: python

        with LongpollConsumer(category="abc123def") as consumer:
            with consumer.iterate() as poll:
                for m in poll.matches:
                    print(m.song.artist, m.song.title)
    """

    def __init__(
        self,
        category: str,
        *,
        httpx_client: httpx.Client | None = None,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ) -> None:
        self._category = category
        self._owned = httpx_client is None
        self._client = httpx_client or httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
            headers={"User-Agent": user_agent()},
        )
        if not self._owned and "User-Agent" not in self._client.headers:
            self._client.headers["User-Agent"] = user_agent()
        self._policy = RetryPolicy(RetryClass.READ, max_retries, backoff_factor)

    def iterate(
        self,
        *,
        since_time: int | None = None,
        timeout: int = 50,
    ) -> _SyncLongpollPoll:
        """Start a poll. Returns a handle with ``matches``, ``notifications``,
        ``errors`` iterators. Use as a context manager for clean shutdown."""
        def fetch(params: dict[str, str]) -> HTTPResponse:
            def _do() -> HTTPResponse:
                r = self._client.get(LONGPOLL_URL, params=params)
                try:
                    body = r.json()
                except Exception:
                    body = None
                return HTTPResponse(
                    json_body=body, http_status=r.status_code,
                    request_id=r.headers.get("x-request-id"), raw_text=r.text,
                )
            return retry_sync(_do, self._policy)

        return _SyncLongpollPoll(fetch, self._category, since_time, timeout)

    def close(self) -> None:
        if self._owned:
            self._client.close()

    def __enter__(self) -> LongpollConsumer:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class AsyncLongpollConsumer:
    """Async tokenless longpoll consumer.

    .. code-block:: python

        async with AsyncLongpollConsumer(category="abc123def") as consumer:
            async with consumer.iterate() as poll:
                async for m in poll.matches:
                    print(m.song.artist, m.song.title)
    """

    def __init__(
        self,
        category: str,
        *,
        httpx_client: httpx.AsyncClient | None = None,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ) -> None:
        self._category = category
        self._owned = httpx_client is None
        self._client = httpx_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
            headers={"User-Agent": user_agent()},
        )
        if not self._owned and "User-Agent" not in self._client.headers:
            self._client.headers["User-Agent"] = user_agent()
        self._policy = RetryPolicy(RetryClass.READ, max_retries, backoff_factor)

    def iterate(
        self,
        *,
        since_time: int | None = None,
        timeout: int = 50,
    ) -> _AsyncLongpollPoll:
        async def fetch(params: dict[str, str]) -> HTTPResponse:
            async def _do() -> HTTPResponse:
                r = await self._client.get(LONGPOLL_URL, params=params)
                try:
                    body = r.json()
                except Exception:
                    body = None
                return HTTPResponse(
                    json_body=body, http_status=r.status_code,
                    request_id=r.headers.get("x-request-id"), raw_text=r.text,
                )
            return await retry_async(_do, self._policy)

        poll = _AsyncLongpollPoll()
        poll._start(_run_longpoll_async(poll, fetch, self._category, since_time, timeout))
        return poll

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()

    async def __aenter__(self) -> AsyncLongpollConsumer:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
