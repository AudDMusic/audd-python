"""Streams namespace — set_callback_url, addStream, longpoll with preflight, etc."""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx

from audd._callbacks import (
    add_return_to_url,
    derive_longpoll_category,
    parse_callback,
)
from audd._http import AsyncHTTPClient, HTTPClient
from audd._retry import RetryPolicy, retry_async, retry_sync
from audd.errors import (
    AudDAPIError,
    AudDConnectionError,
    AudDInvalidRequestError,
    AudDSerializationError,
    AudDServerError,
    raise_from_error_response,
)
from audd.models import Stream, StreamCallbackPayload

API_BASE = "https://api.audd.io"

# Server returns error #19 from getCallbackUrl when no callback URL is configured.
# Catch-all "Internal error" code; we treat it specifically here as the
# no-callback-set signal per docs/captured behavior.
_NO_CALLBACK_ERROR_CODE = 19

PREFLIGHT_NO_CALLBACK_HINT = (
    "Longpoll won't deliver events because no callback URL is configured for this account. "
    "Set one first via streams.set_callback_url(...) — `https://audd.tech/empty/` is fine if "
    "you only want longpolling and don't need a real receiver. "
    "To skip this check, pass skip_callback_check=True."
)


def _decode_success(body: Any, http_status: int, request_id: str | None) -> Any:
    """Inspect a generic response, raise if error, else return body['result']."""
    if not isinstance(body, dict):
        raise AudDSerializationError("Unparseable response")
    if body.get("status") == "error":
        raise_from_error_response(body, http_status=http_status, request_id=request_id)
    if body.get("status") == "success":
        return body.get("result")
    raise AudDServerError(
        error_code=0,
        message=f"Unexpected response status: {body.get('status')!r}",
        http_status=http_status,
        request_id=request_id,
        raw_response=body,
    )


class _StreamsBase:
    def __init__(self, token_getter: Any) -> None:
        # token_getter is a zero-arg callable returning the current token,
        # so derive_longpoll_category honors set_api_token rotations.
        self._token_getter = token_getter

    def derive_longpoll_category(self, radio_id: int) -> str:
        """Compute MD5(MD5(api_token)+str(radio_id))[:9] locally."""
        return derive_longpoll_category(self._token_getter(), radio_id)

    def parse_callback(self, body: dict[str, Any]) -> StreamCallbackPayload:
        return parse_callback(body)


class Streams(_StreamsBase):
    """Sync streams namespace."""

    def __init__(
        self,
        http: HTTPClient,
        read_policy: RetryPolicy,
        mutating_policy: RetryPolicy,
        token_getter: Any,
    ) -> None:
        super().__init__(token_getter)
        self._http = http
        self._read = read_policy
        self._mutating = mutating_policy

    def _post(self, path: str, data: dict[str, Any], policy: RetryPolicy) -> Any:
        def _do() -> Any:
            return self._http.post_form(f"{API_BASE}/{path}/", data=data)

        try:
            resp = retry_sync(_do, policy)
        except httpx.RequestError as exc:
            raise AudDConnectionError(str(exc), original=exc) from exc
        return _decode_success(resp.json_body, resp.http_status, resp.request_id)

    def set_callback_url(
        self,
        url: str,
        *,
        return_metadata: str | list[str] | None = None,
    ) -> None:
        url = add_return_to_url(url, return_metadata)
        self._post("setCallbackUrl", {"url": url}, self._mutating)

    def get_callback_url(self) -> str:
        result = self._post("getCallbackUrl", {}, self._read)
        return str(result)

    def add(
        self,
        url: str,
        radio_id: int,
        *,
        callbacks: str | None = None,
    ) -> None:
        data: dict[str, Any] = {"url": url, "radio_id": str(radio_id)}
        if callbacks is not None:
            data["callbacks"] = callbacks
        self._post("addStream", data, self._mutating)

    def set_url(self, radio_id: int, url: str) -> None:
        self._post(
            "setStreamUrl", {"radio_id": str(radio_id), "url": url}, self._mutating,
        )

    def delete(self, radio_id: int) -> None:
        self._post("deleteStream", {"radio_id": str(radio_id)}, self._mutating)

    def list(self) -> list[Stream]:
        result = self._post("getStreams", {}, self._read) or []
        return [Stream.model_validate(s) for s in result]

    def longpoll(
        self,
        category: str,
        *,
        since_time: int | None = None,
        timeout: int = 50,
        skip_callback_check: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Yield successive longpoll responses (timeout or event variants).

        On entry: preflights getCallbackUrl unless skip_callback_check=True.
        """
        if not skip_callback_check:
            try:
                self.get_callback_url()
            except AudDAPIError as exc:
                if exc.error_code == _NO_CALLBACK_ERROR_CODE:
                    raise AudDInvalidRequestError(
                        error_code=0,
                        message=PREFLIGHT_NO_CALLBACK_HINT,
                        http_status=exc.http_status,
                        request_id=exc.request_id,
                    ) from None
                raise

        cur_since = since_time

        def _one() -> Any:
            params: dict[str, Any] = {"category": category, "timeout": str(timeout)}
            if cur_since is not None:
                params["since_time"] = str(cur_since)
            return self._http.get(f"{API_BASE}/longpoll/", params=params)

        while True:
            try:
                resp = retry_sync(_one, self._read)
            except httpx.RequestError as exc:
                raise AudDConnectionError(str(exc), original=exc) from exc
            body = resp.json_body
            if not isinstance(body, dict):
                raise AudDSerializationError("Unparseable longpoll response")
            yield body
            ts = body.get("timestamp")
            if isinstance(ts, int):
                cur_since = ts


class AsyncStreams(_StreamsBase):
    """Async streams namespace — mirror of Streams."""

    def __init__(
        self,
        http: AsyncHTTPClient,
        read_policy: RetryPolicy,
        mutating_policy: RetryPolicy,
        token_getter: Any,
    ) -> None:
        super().__init__(token_getter)
        self._http = http
        self._read = read_policy
        self._mutating = mutating_policy

    async def _post(self, path: str, data: dict[str, Any], policy: RetryPolicy) -> Any:
        async def _do() -> Any:
            return await self._http.post_form(f"{API_BASE}/{path}/", data=data)

        try:
            resp = await retry_async(_do, policy)
        except httpx.RequestError as exc:
            raise AudDConnectionError(str(exc), original=exc) from exc
        return _decode_success(resp.json_body, resp.http_status, resp.request_id)

    async def set_callback_url(
        self,
        url: str,
        *,
        return_metadata: str | list[str] | None = None,
    ) -> None:
        url = add_return_to_url(url, return_metadata)
        await self._post("setCallbackUrl", {"url": url}, self._mutating)

    async def get_callback_url(self) -> str:
        return str(await self._post("getCallbackUrl", {}, self._read))

    async def add(
        self,
        url: str,
        radio_id: int,
        *,
        callbacks: str | None = None,
    ) -> None:
        data: dict[str, Any] = {"url": url, "radio_id": str(radio_id)}
        if callbacks is not None:
            data["callbacks"] = callbacks
        await self._post("addStream", data, self._mutating)

    async def set_url(self, radio_id: int, url: str) -> None:
        await self._post(
            "setStreamUrl", {"radio_id": str(radio_id), "url": url}, self._mutating,
        )

    async def delete(self, radio_id: int) -> None:
        await self._post("deleteStream", {"radio_id": str(radio_id)}, self._mutating)

    async def list(self) -> list[Stream]:
        result = await self._post("getStreams", {}, self._read) or []
        return [Stream.model_validate(s) for s in result]

    async def longpoll(
        self,
        category: str,
        *,
        since_time: int | None = None,
        timeout: int = 50,
        skip_callback_check: bool = False,
    ) -> AsyncIterator[dict[str, Any]]:
        if not skip_callback_check:
            try:
                await self.get_callback_url()
            except AudDAPIError as exc:
                if exc.error_code == _NO_CALLBACK_ERROR_CODE:
                    raise AudDInvalidRequestError(
                        error_code=0,
                        message=PREFLIGHT_NO_CALLBACK_HINT,
                        http_status=exc.http_status,
                        request_id=exc.request_id,
                    ) from None
                raise

        cur_since = since_time

        while True:
            params: dict[str, Any] = {"category": category, "timeout": str(timeout)}
            if cur_since is not None:
                params["since_time"] = str(cur_since)

            # Bind params explicitly to defeat B023 (closure over loop variable).
            async def _one(p: dict[str, Any] = params) -> Any:
                return await self._http.get(f"{API_BASE}/longpoll/", params=p)

            try:
                resp = await retry_async(_one, self._read)
            except httpx.RequestError as exc:
                raise AudDConnectionError(str(exc), original=exc) from exc
            body = resp.json_body
            if not isinstance(body, dict):
                raise AudDSerializationError("Unparseable longpoll response")
            yield body
            ts = body.get("timestamp")
            if isinstance(ts, int):
                cur_since = ts
