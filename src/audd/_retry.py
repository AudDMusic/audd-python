"""Cost-aware retry policy."""
from __future__ import annotations

import asyncio
import enum
import random
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Callable, TypeVar

import httpx

from audd._http import HTTPResponse


class RetryClass(enum.Enum):
    """Determines which conditions are retryable for a given endpoint.

    READ        — idempotent reads (streams.list, streams.get_callback_url):
                  retry on 408/429/5xx + any connection error.
    RECOGNITION — recognize, recognize_enterprise, advanced.find_lyrics:
                  retry on pre-upload connection failures + 5xx.
                  DO NOT retry on read-timeout-after-upload (cost protection).
    MUTATING    — streams.set_callback_url, streams.add, streams.delete, etc.,
                  custom_catalog.add: retry only on pre-upload connection failures.
                  DO NOT retry 5xx (the side effect may have happened).
    """

    READ = "read"
    RECOGNITION = "recognition"
    MUTATING = "mutating"


@dataclass(frozen=True)
class RetryPolicy:
    retry_class: RetryClass
    max_attempts: int = 3
    backoff_factor: float = 0.5
    backoff_max: float = 30.0


T = TypeVar("T")


def _sync_sleep(seconds: float) -> None:
    time.sleep(seconds)


async def _async_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _backoff_delay(attempt: int, policy: RetryPolicy) -> float:
    base = min(policy.backoff_factor * (2**attempt), policy.backoff_max)
    jitter = 0.5 + random.random()
    return float(base * jitter)


def _is_pre_upload_connection_error(exc: BaseException) -> bool:
    """Errors raised before the request body finished uploading (safe to retry)."""
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.WriteError))


_HTTP_REQUEST_TIMEOUT = 408
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR_FLOOR = 500


def _should_retry_response(resp: HTTPResponse, retry_class: RetryClass) -> bool:
    s = resp.http_status
    if retry_class == RetryClass.READ:
        retryable_specific = (_HTTP_REQUEST_TIMEOUT, _HTTP_TOO_MANY_REQUESTS)
        return s in retryable_specific or s >= _HTTP_SERVER_ERROR_FLOOR
    if retry_class == RetryClass.RECOGNITION:
        return s >= _HTTP_SERVER_ERROR_FLOOR
    if retry_class == RetryClass.MUTATING:
        return False
    raise AssertionError(f"unhandled RetryClass {retry_class!r}")


def _should_retry_exception(exc: BaseException, retry_class: RetryClass) -> bool:
    if retry_class == RetryClass.READ:
        return isinstance(exc, httpx.RequestError)
    if retry_class == RetryClass.RECOGNITION:
        return _is_pre_upload_connection_error(exc)
    if retry_class == RetryClass.MUTATING:
        return _is_pre_upload_connection_error(exc)
    raise AssertionError(f"unhandled RetryClass {retry_class!r}")


def retry_sync(fn: Callable[[], HTTPResponse], policy: RetryPolicy) -> HTTPResponse:
    last_exc: BaseException | None = None
    last_resp: HTTPResponse | None = None
    for attempt in range(policy.max_attempts):
        try:
            resp = fn()
        except BaseException as exc:
            last_exc = exc
            last_resp = None
            if not _should_retry_exception(exc, policy.retry_class):
                raise
            if attempt + 1 >= policy.max_attempts:
                raise
            _sync_sleep(_backoff_delay(attempt, policy))
            continue

        if not _should_retry_response(resp, policy.retry_class):
            return resp
        last_resp = resp
        last_exc = None
        if attempt + 1 >= policy.max_attempts:
            return resp
        _sync_sleep(_backoff_delay(attempt, policy))
    if last_resp is not None:
        return last_resp
    assert last_exc is not None
    raise last_exc


async def retry_async(
    fn: Callable[[], Awaitable[HTTPResponse]],
    policy: RetryPolicy,
) -> HTTPResponse:
    last_exc: BaseException | None = None
    last_resp: HTTPResponse | None = None
    for attempt in range(policy.max_attempts):
        try:
            resp = await fn()
        except BaseException as exc:
            last_exc = exc
            last_resp = None
            if not _should_retry_exception(exc, policy.retry_class):
                raise
            if attempt + 1 >= policy.max_attempts:
                raise
            await _async_sleep(_backoff_delay(attempt, policy))
            continue

        if not _should_retry_response(resp, policy.retry_class):
            return resp
        last_resp = resp
        last_exc = None
        if attempt + 1 >= policy.max_attempts:
            return resp
        await _async_sleep(_backoff_delay(attempt, policy))
    if last_resp is not None:
        return last_resp
    assert last_exc is not None
    raise last_exc
