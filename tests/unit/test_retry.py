"""Unit tests for the cost-aware retry policy."""
from __future__ import annotations

import httpx
import pytest

from audd._http import HTTPResponse
from audd._retry import RetryClass, RetryPolicy, retry_async, retry_sync


def _ok(status: int = 200) -> HTTPResponse:
    return HTTPResponse(json_body={"status": "success"}, http_status=status, request_id=None, raw_text="")


def _err(status: int) -> HTTPResponse:
    return HTTPResponse(json_body={"status": "error"}, http_status=status, request_id=None, raw_text="")


class _Counter:
    def __init__(self, responses):
        self.responses = list(responses)
        self.attempts = 0

    def __call__(self):
        self.attempts += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_read_endpoint_retries_on_5xx_then_succeeds() -> None:
    policy = RetryPolicy(retry_class=RetryClass.READ, max_attempts=3, backoff_factor=0)
    fn = _Counter([_err(503), _err(503), _ok()])
    result = retry_sync(fn, policy)
    assert result.http_status == 200
    assert fn.attempts == 3


def test_read_endpoint_gives_up_after_max_attempts() -> None:
    policy = RetryPolicy(retry_class=RetryClass.READ, max_attempts=3, backoff_factor=0)
    fn = _Counter([_err(503), _err(503), _err(503), _err(503)])
    result = retry_sync(fn, policy)
    assert result.http_status == 503
    assert fn.attempts == 3


def test_mutating_endpoint_does_not_retry_5xx() -> None:
    """Mutating endpoints don't retry server errors — side effects may have happened."""
    policy = RetryPolicy(retry_class=RetryClass.MUTATING, max_attempts=3, backoff_factor=0)
    fn = _Counter([_err(503), _ok()])
    result = retry_sync(fn, policy)
    assert result.http_status == 503
    assert fn.attempts == 1


def test_mutating_endpoint_retries_pre_upload_connection_error() -> None:
    policy = RetryPolicy(retry_class=RetryClass.MUTATING, max_attempts=3, backoff_factor=0)
    fn = _Counter([httpx.ConnectError("dns failed"), _ok()])
    result = retry_sync(fn, policy)
    assert result.http_status == 200
    assert fn.attempts == 2


def test_recognition_endpoint_does_not_retry_post_upload_read_timeout() -> None:
    """Recognition endpoints don't retry read timeouts after upload completed (cost protection)."""
    policy = RetryPolicy(retry_class=RetryClass.RECOGNITION, max_attempts=3, backoff_factor=0)
    fn = _Counter([httpx.ReadTimeout("server slow"), _ok()])
    with pytest.raises(httpx.ReadTimeout):
        retry_sync(fn, policy)
    assert fn.attempts == 1


def test_recognition_endpoint_retries_5xx() -> None:
    policy = RetryPolicy(retry_class=RetryClass.RECOGNITION, max_attempts=3, backoff_factor=0)
    fn = _Counter([_err(502), _ok()])
    result = retry_sync(fn, policy)
    assert result.http_status == 200
    assert fn.attempts == 2


def test_recognition_endpoint_retries_pre_upload_connect_error() -> None:
    policy = RetryPolicy(retry_class=RetryClass.RECOGNITION, max_attempts=3, backoff_factor=0)
    fn = _Counter([httpx.ConnectError("dns"), _ok()])
    result = retry_sync(fn, policy)
    assert result.http_status == 200
    assert fn.attempts == 2


def test_disable_retries_with_max_attempts_1() -> None:
    policy = RetryPolicy(retry_class=RetryClass.READ, max_attempts=1, backoff_factor=0)
    fn = _Counter([_err(503), _ok()])
    result = retry_sync(fn, policy)
    assert result.http_status == 503
    assert fn.attempts == 1


@pytest.mark.asyncio
async def test_async_retries() -> None:
    policy = RetryPolicy(retry_class=RetryClass.READ, max_attempts=3, backoff_factor=0)
    counter = {"n": 0}

    async def fn():
        counter["n"] += 1
        if counter["n"] < 3:
            return _err(502)
        return _ok()

    result = await retry_async(fn, policy)
    assert result.http_status == 200
    assert counter["n"] == 3
