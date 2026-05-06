"""Tokenless longpoll consumer for browser/widget/extension use cases.

Carries no api_token. The category alone authorizes the subscription. The
user/server who derived the category is responsible for ensuring a callback
URL is set on their account (we can't preflight that without a token).

Behavior:
* HTTP non-2xx → raises AudDServerError
* JSON decode failure → raises AudDSerializationError
* Retries (READ class) on 5xx + connection errors
* Configurable max_retries / backoff_factor
* Context-manager protocol
"""
from __future__ import annotations

import json as _json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx

from audd._http import HTTPResponse
from audd._retry import RetryClass, RetryPolicy, retry_async, retry_sync
from audd._user_agent import user_agent
from audd.errors import (
    AudDConnectionError,
    AudDSerializationError,
    AudDServerError,
)

LONGPOLL_URL = "https://api.audd.io/longpoll/"
_HTTP_CLIENT_ERROR_FLOOR = 400


def _decode(json_body: Any, http_status: int, raw_text: str) -> dict[str, Any]:
    if http_status >= _HTTP_CLIENT_ERROR_FLOOR:
        raise AudDServerError(
            error_code=0,
            message=f"Longpoll endpoint returned HTTP {http_status}",
            http_status=http_status,
            request_id=None,
            raw_response=json_body if json_body is not None else raw_text,
        )
    if not isinstance(json_body, dict):
        try:
            parsed = _json.loads(raw_text) if raw_text else None
        except _json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, dict):
            raise AudDSerializationError(
                "Longpoll response was not a JSON object", raw_text=raw_text,
            )
        return parsed
    return json_body


class LongpollConsumer:
    """Sync tokenless longpoll consumer."""

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
    ) -> Iterator[dict[str, Any]]:
        cur_since = since_time
        while True:
            params: dict[str, str] = {"category": self._category, "timeout": str(timeout)}
            if cur_since is not None:
                params["since_time"] = str(cur_since)

            def _do(p: dict[str, str] = params) -> HTTPResponse:
                r = self._client.get(LONGPOLL_URL, params=p)
                try:
                    body: Any = r.json()
                except Exception:
                    body = None
                return HTTPResponse(
                    json_body=body, http_status=r.status_code,
                    request_id=r.headers.get("x-request-id"), raw_text=r.text,
                )

            try:
                resp = retry_sync(_do, self._policy)
            except httpx.RequestError as exc:
                raise AudDConnectionError(str(exc), original=exc) from exc
            body = _decode(resp.json_body, resp.http_status, resp.raw_text)
            yield body
            ts = body.get("timestamp")
            if isinstance(ts, int):
                cur_since = ts

    def close(self) -> None:
        if self._owned:
            self._client.close()

    def __enter__(self) -> LongpollConsumer:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class AsyncLongpollConsumer:
    """Async tokenless longpoll consumer."""

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

    async def iterate(
        self,
        *,
        since_time: int | None = None,
        timeout: int = 50,
    ) -> AsyncIterator[dict[str, Any]]:
        cur_since = since_time
        while True:
            params: dict[str, str] = {"category": self._category, "timeout": str(timeout)}
            if cur_since is not None:
                params["since_time"] = str(cur_since)

            async def _do(p: dict[str, str] = params) -> HTTPResponse:
                r = await self._client.get(LONGPOLL_URL, params=p)
                try:
                    body: Any = r.json()
                except Exception:
                    body = None
                return HTTPResponse(
                    json_body=body, http_status=r.status_code,
                    request_id=r.headers.get("x-request-id"), raw_text=r.text,
                )

            try:
                resp = await retry_async(_do, self._policy)
            except httpx.RequestError as exc:
                raise AudDConnectionError(str(exc), original=exc) from exc
            body = _decode(resp.json_body, resp.http_status, resp.raw_text)
            yield body
            ts = body.get("timestamp")
            if isinstance(ts, int):
                cur_since = ts

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()

    async def __aenter__(self) -> AsyncLongpollConsumer:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()
