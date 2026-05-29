"""Advanced namespace — lyrics search + raw escape hatch.

Reach this only via audd.advanced.* — deliberately not on the main client.
"""
from __future__ import annotations

from typing import Any

import httpx

from audd._http import AsyncHTTPClient, HTTPClient
from audd._retry import RetryPolicy, retry_async, retry_sync
from audd.errors import (
    AudDConnectionError,
    AudDSerializationError,
    raise_from_error_response,
)
from audd.models import LyricsResult, _coerce_model_list

API_BASE = "https://api.audd.io"


class Advanced:
    def __init__(self, http: HTTPClient, read: RetryPolicy) -> None:
        self._http = http
        self._read = read

    def find_lyrics(self, query: str) -> list[LyricsResult]:
        body = self.raw_request("findLyrics", {"q": query})
        if body.get("status") == "error":
            raise_from_error_response(body, http_status=200, request_id=None)
        return _coerce_model_list(body.get("result"), LyricsResult)

    def raw_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Hit any AudD endpoint by method name and return the raw JSON dict.

        Useful for endpoints not yet wrapped by typed methods on this SDK.
        """
        data = dict(params or {})

        def _do() -> Any:
            return self._http.post_form(f"{API_BASE}/{method}/", data=data)

        try:
            resp = retry_sync(_do, self._read)
        except httpx.RequestError as exc:
            raise AudDConnectionError(str(exc), original=exc) from exc
        body = resp.json_body
        if not isinstance(body, dict):
            raise AudDSerializationError("Unparseable response")
        return body


class AsyncAdvanced:
    def __init__(self, http: AsyncHTTPClient, read: RetryPolicy) -> None:
        self._http = http
        self._read = read

    async def find_lyrics(self, query: str) -> list[LyricsResult]:
        body = await self.raw_request("findLyrics", {"q": query})
        if body.get("status") == "error":
            raise_from_error_response(body, http_status=200, request_id=None)
        return _coerce_model_list(body.get("result"), LyricsResult)

    async def raw_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = dict(params or {})

        async def _do() -> Any:
            return await self._http.post_form(f"{API_BASE}/{method}/", data=data)

        try:
            resp = await retry_async(_do, self._read)
        except httpx.RequestError as exc:
            raise AudDConnectionError(str(exc), original=exc) from exc
        body = resp.json_body
        if not isinstance(body, dict):
            raise AudDSerializationError("Unparseable response")
        return body
