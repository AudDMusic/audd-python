"""Custom-catalog endpoint. NOT for recognition — see method docstrings."""
from __future__ import annotations

from typing import Any

import httpx

from audd._http import AsyncHTTPClient, HTTPClient
from audd._retry import RetryPolicy, retry_async, retry_sync
from audd._source import Source, prepare_source
from audd.errors import (
    AudDConnectionError,
    AudDSerializationError,
    AudDServerError,
    raise_from_error_response,
)

UPLOAD_URL = "https://api.audd.io/upload/"


def _decode_success(body: Any, http_status: int, request_id: str | None) -> None:
    if not isinstance(body, dict):
        raise AudDSerializationError("Unparseable response")
    if body.get("status") == "error":
        raise_from_error_response(
            body,
            http_status=http_status,
            request_id=request_id,
            custom_catalog_context=True,
        )
    if body.get("status") != "success":
        raise AudDServerError(
            error_code=0,
            message=f"Unexpected status: {body.get('status')!r}",
            http_status=http_status,
            request_id=request_id,
            raw_response=body,
        )


class CustomCatalog:
    def __init__(self, http: HTTPClient, mutating: RetryPolicy) -> None:
        self._http = http
        self._mutating = mutating

    def add(self, audio_id: int, source: Source) -> None:
        """**This is NOT how you submit audio for music recognition.** For
        recognition, use ``recognize()`` (or ``recognize_enterprise()`` for files
        longer than 25 seconds). This method adds a song to your **private
        fingerprint catalog** so AudD's recognition can later identify *your own*
        tracks for *your account only*. Requires special access — contact
        api@audd.io if you need it enabled.

        Calling this again with the same ``audio_id`` re-fingerprints that slot.
        There is no public list/delete endpoint; track ``audio_id`` ↔ song
        mappings on your side.
        """
        reopen = prepare_source(source)

        def _do() -> Any:
            data, files = reopen()
            data["audio_id"] = str(audio_id)
            return self._http.post_form(UPLOAD_URL, data=data, files=files)

        try:
            resp = retry_sync(_do, self._mutating)
        except httpx.RequestError as exc:
            raise AudDConnectionError(str(exc), original=exc) from exc
        _decode_success(resp.json_body, resp.http_status, resp.request_id)


class AsyncCustomCatalog:
    def __init__(self, http: AsyncHTTPClient, mutating: RetryPolicy) -> None:
        self._http = http
        self._mutating = mutating

    async def add(self, audio_id: int, source: Source) -> None:
        """See :meth:`CustomCatalog.add`. **NOT for music recognition** —
        use recognize() / recognize_enterprise()."""
        reopen = prepare_source(source)

        async def _do() -> Any:
            data, files = reopen()
            data["audio_id"] = str(audio_id)
            return await self._http.post_form(UPLOAD_URL, data=data, files=files)

        try:
            resp = await retry_async(_do, self._mutating)
        except httpx.RequestError as exc:
            raise AudDConnectionError(str(exc), original=exc) from exc
        _decode_success(resp.json_body, resp.http_status, resp.request_id)
