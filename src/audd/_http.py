"""HTTP transport. Sync (HTTPClient) and async (AsyncHTTPClient) wrappers around httpx."""
from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from audd._user_agent import user_agent

DEFAULT_TIMEOUTS = httpx.Timeout(connect=30.0, read=60.0, write=60.0, pool=30.0)
ENTERPRISE_TIMEOUTS = httpx.Timeout(connect=30.0, read=3600.0, write=3600.0, pool=30.0)


@dataclass(frozen=True)
class HTTPResponse:
    json_body: Any  # parsed JSON body, or None on parse failure
    http_status: int
    request_id: str | None  # x-request-id header, if present
    raw_text: str  # original response body text (for AudDSerializationError diagnostics)


def _request_id(headers: Mapping[str, str]) -> str | None:
    return headers.get("x-request-id") or headers.get("X-Request-ID")


class HTTPClient:
    def __init__(
        self,
        api_token: str,
        *,
        timeouts: httpx.Timeout = DEFAULT_TIMEOUTS,
        httpx_client: httpx.Client | None = None,
    ) -> None:
        self._token_lock = threading.Lock()
        self._api_token = api_token
        self._owned = httpx_client is None
        self._client = httpx_client or httpx.Client(
            timeout=timeouts,
            headers={"User-Agent": user_agent()},
        )
        if not self._owned and "User-Agent" not in self._client.headers:
            self._client.headers["User-Agent"] = user_agent()

    def set_api_token(self, new_token: str) -> None:
        """Atomically swap the token used for subsequent requests."""
        with self._token_lock:
            self._api_token = new_token

    def _current_token(self) -> str:
        with self._token_lock:
            return self._api_token

    def post_form(
        self,
        url: str,
        data: dict[str, Any],
        *,
        timeout: httpx.Timeout | None = None,
        files: Mapping[str, Any] | None = None,
    ) -> HTTPResponse:
        """POST multipart/form-data with api_token always included."""
        full_data = dict(data)
        full_data["api_token"] = self._current_token()
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if files is not None:
            kwargs["files"] = files
        r = self._client.post(url, data=full_data, **kwargs)
        return self._wrap(r)

    def get(
        self,
        url: str,
        params: dict[str, Any],
        *,
        timeout: httpx.Timeout | None = None,
    ) -> HTTPResponse:
        full = dict(params)
        full.setdefault("api_token", self._current_token())
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        r = self._client.get(url, params=full, **kwargs)
        return self._wrap(r)

    def _wrap(self, r: httpx.Response) -> HTTPResponse:
        try:
            body: Any = r.json()
        except Exception:
            body = None
        return HTTPResponse(
            json_body=body,
            http_status=r.status_code,
            request_id=_request_id(r.headers),
            raw_text=r.text,
        )

    def close(self) -> None:
        if self._owned:
            self._client.close()


class AsyncHTTPClient:
    def __init__(
        self,
        api_token: str,
        *,
        timeouts: httpx.Timeout = DEFAULT_TIMEOUTS,
        httpx_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token_lock = threading.Lock()
        self._api_token = api_token
        self._owned = httpx_client is None
        self._client = httpx_client or httpx.AsyncClient(
            timeout=timeouts,
            headers={"User-Agent": user_agent()},
        )
        if not self._owned and "User-Agent" not in self._client.headers:
            self._client.headers["User-Agent"] = user_agent()

    def set_api_token(self, new_token: str) -> None:
        """Atomically swap the token used for subsequent requests."""
        with self._token_lock:
            self._api_token = new_token

    def _current_token(self) -> str:
        with self._token_lock:
            return self._api_token

    async def post_form(
        self,
        url: str,
        data: dict[str, Any],
        *,
        timeout: httpx.Timeout | None = None,
        files: Mapping[str, Any] | None = None,
    ) -> HTTPResponse:
        full_data = dict(data)
        full_data["api_token"] = self._current_token()
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if files is not None:
            kwargs["files"] = files
        r = await self._client.post(url, data=full_data, **kwargs)
        return _wrap_async(r)

    async def get(
        self,
        url: str,
        params: dict[str, Any],
        *,
        timeout: httpx.Timeout | None = None,
    ) -> HTTPResponse:
        full = dict(params)
        full.setdefault("api_token", self._current_token())
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        r = await self._client.get(url, params=full, **kwargs)
        return _wrap_async(r)

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()


def _wrap_async(r: httpx.Response) -> HTTPResponse:
    try:
        body: Any = r.json()
    except Exception:
        body = None
    return HTTPResponse(
        json_body=body,
        http_status=r.status_code,
        request_id=_request_id(r.headers),
        raw_text=r.text,
    )
