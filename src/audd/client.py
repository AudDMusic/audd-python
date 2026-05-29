"""Top-level AudD / AsyncAudD clients."""
from __future__ import annotations

import os
import threading
import warnings
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Literal

import httpx

from audd._http import (
    ENTERPRISE_TIMEOUTS,
    AsyncHTTPClient,
    HTTPClient,
    HTTPResponse,
)
from audd._retry import RetryClass, RetryPolicy, retry_async, retry_sync
from audd._source import Source, prepare_source
from audd.errors import (
    AudDConnectionError,
    AudDSerializationError,
    AudDServerError,
    raise_from_error_response,
)
from audd.models import EnterpriseChunkResult, EnterpriseMatch, RecognitionResult

API_BASE = "https://api.audd.io"
ENTERPRISE_BASE = "https://enterprise.audd.io"

# Environment variable consulted when api_token is not passed explicitly.
_TOKEN_ENV_VAR = "AUDD_API_TOKEN"

# Code 51 is server-side soft-deprecation. We emit a DeprecationWarning and
# pass through the result if one is present.
_DEPRECATED_PARAMS_CODE = 51
_HTTP_CLIENT_ERROR_FLOOR = 400


def _resolve_token(api_token: str | None) -> str:
    """Resolve api_token from explicit arg → AUDD_API_TOKEN env var → error.

    Raising loudly here is deliberate: silently allowing an empty token would
    surface as a confusing #901 from the server later.
    """
    if api_token:
        return api_token
    env = os.environ.get(_TOKEN_ENV_VAR)
    if env:
        return env
    raise ValueError(
        "AudD api_token not supplied and AUDD_API_TOKEN env var is unset. "
        "Get a token at https://dashboard.audd.io and pass it as "
        "AudD(api_token=...) or set AUDD_API_TOKEN.",
    )


EventKind = Literal["request", "response", "exception"]


@dataclass(frozen=True)
class AudDEvent:
    """Inspection event emitted by the SDK request lifecycle.

    Hooks receive these via the ``on_event`` callback. Frozen, plain-data,
    never includes the api_token or request body bytes.
    """

    kind: EventKind
    method: str  # AudD method name, e.g. "recognize", "addStream"
    url: str
    request_id: str | None = None
    http_status: int | None = None
    elapsed_ms: float | None = None
    error_code: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)


# Type alias for the on_event hook signature.
OnEventHook = Callable[[AudDEvent], None]


def _safe_emit(hook: OnEventHook | None, event: AudDEvent) -> None:
    """Invoke ``hook`` swallowing any exception so observability never breaks
    the request path."""
    if hook is None:
        return
    try:
        hook(event)
    except Exception:
        import logging
        logging.getLogger("audd").debug("on_event hook raised; suppressed", exc_info=True)


def _format_return(return_metadata: str | Iterable[str] | None) -> str | None:
    if return_metadata is None:
        return None
    if isinstance(return_metadata, str):
        return return_metadata
    return ",".join(return_metadata)


def _build_enterprise_fields(
    return_str: str | None,
    skip: int | None,
    every: int | None,
    limit: int | None,
    skip_first_seconds: int | None,
    use_timecode: bool | None,
    accurate_offsets: bool | None,
) -> dict[str, str]:
    fields: dict[str, str] = {}
    if return_str is not None:
        fields["return"] = return_str
    for k, v in (
        ("skip", skip), ("every", every), ("limit", limit),
        ("skip_first_seconds", skip_first_seconds),
    ):
        if v is not None:
            fields[k] = str(v)
    if use_timecode is not None:
        fields["use_timecode"] = "true" if use_timecode else "false"
    if accurate_offsets is not None:
        fields["accurate_offsets"] = "true" if accurate_offsets else "false"
    return fields


def _is_deprecation_pass_through(body: dict[str, Any]) -> bool:
    """Code 51 + a usable result → warn and return; don't raise.

    The server marks a parameter as deprecated but still fulfilled the request.
    """
    err = body.get("error") or {}
    return err.get("error_code") == _DEPRECATED_PARAMS_CODE and body.get("result") is not None


def _maybe_warn_and_strip(body: dict[str, Any]) -> None:
    """If body carries a code-51 deprecation warning + a usable result, emit the
    warning and rewrite the body to look like a normal success response."""
    if _is_deprecation_pass_through(body):
        msg = (body.get("error") or {}).get("error_message", "Deprecated parameter used")
        warnings.warn(str(msg), DeprecationWarning, stacklevel=4)
        body.pop("error", None)
        body["status"] = "success"


def _decode_or_raise(resp: HTTPResponse) -> dict[str, Any]:
    """Inspect a response, raise typed errors for obvious failures, else return the body dict.

    Distinguishes:
    * non-2xx HTTP with non-JSON body → AudDServerError (preserves status)
    * 2xx with non-JSON body → AudDSerializationError
    * status=error with code-51 + result → emit DeprecationWarning, strip error, fall through
    * status=error otherwise → raise typed exception
    * status=success or now-error-stripped → return body
    """
    body = resp.json_body
    if not isinstance(body, dict):
        if resp.http_status >= _HTTP_CLIENT_ERROR_FLOOR:
            raise AudDServerError(
                error_code=0,
                message=f"HTTP {resp.http_status} with non-JSON response body",
                http_status=resp.http_status,
                request_id=resp.request_id,
                raw_response=resp.raw_text,
            )
        raise AudDSerializationError("Unparseable response", raw_text=resp.raw_text)

    _maybe_warn_and_strip(body)

    if body.get("status") == "error":
        raise_from_error_response(
            body, http_status=resp.http_status, request_id=resp.request_id,
        )
    if body.get("status") == "success":
        return body
    raise AudDServerError(
        error_code=0,
        message=f"Unexpected response status: {body.get('status')!r}",
        http_status=resp.http_status,
        request_id=resp.request_id,
        raw_response=body,
    )


def _decode_recognize(resp: HTTPResponse) -> RecognitionResult | None:
    body = _decode_or_raise(resp)
    result = body.get("result")
    # No-match responses carry result: null (or, rarely, a non-object falsy
    # value). Treat anything that isn't a dict as "no match" rather than raising.
    if not isinstance(result, dict):
        return None
    return RecognitionResult.model_validate(result)


def _decode_enterprise(resp: HTTPResponse) -> list[EnterpriseMatch]:
    body = _decode_or_raise(resp)
    chunks_raw = body.get("result")
    out: list[EnterpriseMatch] = []
    # A successful response must never raise on a missing/odd-typed result.
    # Skip anything that isn't a chunk object instead of aborting the parse.
    if not isinstance(chunks_raw, list):
        return out
    for chunk_dict in chunks_raw:
        try:
            chunk = EnterpriseChunkResult.model_validate(chunk_dict)
        except Exception:  # noqa: BLE001 — degrade, never raise on response parse
            continue
        out.extend(chunk.songs)
    return out


class _BaseClient:
    """Shared sync/async configuration knobs.

    Holds a lock-guarded api_token so callers can rotate it via
    `set_api_token(new_token)` without aborting in-flight requests.
    """

    def __init__(
        self,
        api_token: str,
        *,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        on_event: OnEventHook | None = None,
    ) -> None:
        self._token_lock = threading.Lock()
        self._api_token = api_token
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor
        self._on_event = on_event

    @property
    def api_token(self) -> str:
        with self._token_lock:
            return self._api_token

    def set_api_token(self, new_token: str) -> None:
        """Rotate the api_token used for subsequent requests.

        In-flight requests continue with the previous token (no abort).
        Thread-safe — safe to call concurrently with recognize() and friends.
        """
        if not new_token:
            raise ValueError("new_token must be a non-empty string")
        with self._token_lock:
            self._api_token = new_token
        # Propagate to existing transport(s).
        for http in self._http_layers():
            http.set_api_token(new_token)

    def _http_layers(self) -> Iterable[Any]:
        """Subclass hook: yield underlying HTTP clients to update on rotation."""
        return ()

    def _read_policy(self) -> RetryPolicy:
        return RetryPolicy(RetryClass.READ, self._max_retries, self._backoff_factor)

    def _recognition_policy(self) -> RetryPolicy:
        return RetryPolicy(RetryClass.RECOGNITION, self._max_retries, self._backoff_factor)

    def _mutating_policy(self) -> RetryPolicy:
        return RetryPolicy(RetryClass.MUTATING, self._max_retries, self._backoff_factor)

    def _no_retry_policy(self) -> RetryPolicy:
        # custom_catalog.add is metered: silently retrying a transient transport
        # failure could double-charge the same audio fingerprinting. A clean error
        # is preferable. Reuse MUTATING semantics (no 5xx retries) but pin
        # max_attempts=1 so even pre-upload connection errors aren't retried.
        return RetryPolicy(RetryClass.MUTATING, max_attempts=1, backoff_factor=self._backoff_factor)


class AudD(_BaseClient):
    """Sync client for the AudD music recognition API.

    The ``api_token`` may be omitted; in that case the SDK reads
    ``AUDD_API_TOKEN`` from the environment (raising ``ValueError`` if neither
    is set).

    For per-call cancellation, pass ``timeout=`` (httpx will raise on
    expiry). The SDK does not attempt to abort in-flight requests on its own,
    and **server-side metering is consumed regardless of whether the local
    request is cancelled** — so cancellation aborts only the local I/O wait,
    not the server-side cost.
    """

    def __init__(
        self,
        api_token: str | None = None,
        *,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        httpx_client: httpx.Client | None = None,
        on_event: OnEventHook | None = None,
    ) -> None:
        token = _resolve_token(api_token)
        super().__init__(
            token,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            on_event=on_event,
        )
        self._http = HTTPClient(token, httpx_client=httpx_client)
        self._enterprise_http = HTTPClient(
            token,
            timeouts=ENTERPRISE_TIMEOUTS,
            httpx_client=httpx_client,
        )
        self._streams: Any = None
        self._custom_catalog: Any = None
        self._advanced: Any = None

    def _http_layers(self) -> Iterable[Any]:
        return (self._http, self._enterprise_http)

    @property
    def streams(self) -> Any:
        if self._streams is None:
            from audd.streams import Streams

            self._streams = Streams(
                self._http, self._read_policy(), self._mutating_policy(),
                lambda: self.api_token,
            )
        return self._streams

    @property
    def custom_catalog(self) -> Any:
        if self._custom_catalog is None:
            from audd.custom_catalog import CustomCatalog

            self._custom_catalog = CustomCatalog(self._http, self._no_retry_policy())
        return self._custom_catalog

    @property
    def advanced(self) -> Any:
        if self._advanced is None:
            from audd.advanced import Advanced

            # Advanced uses RECOGNITION policy: find_lyrics is metered and shouldn't
            # double-bill on post-upload read timeout.
            self._advanced = Advanced(self._http, self._recognition_policy())
        return self._advanced

    def recognize(
        self,
        source: Source,
        *,
        return_metadata: str | Iterable[str] | None = None,
        market: str | None = None,
        timeout: float | None = None,
        extra_parameters: dict[str, str] | None = None,
    ) -> RecognitionResult | None:
        """Recognize a short clip. Wrap in ``asyncio.wait_for`` (on AsyncAudD)
        or use ``timeout=`` here for cancellation; note that **server-side
        metering still consumes credit even if the local call is cancelled**.

        ``extra_parameters`` lets you pass additional form fields the typed
        kwargs don't cover — undocumented parameters or beta features.
        Typed kwargs (``return_metadata``, ``market``) take precedence on collision.
        """
        reopen = prepare_source(source)
        ret = _format_return(return_metadata)
        url = f"{API_BASE}/"
        hook = self._on_event
        _safe_emit(hook, AudDEvent(kind="request", method="recognize", url=url))
        started = monotonic()

        def _do() -> Any:
            data, files = reopen()
            if extra_parameters:
                data.update(extra_parameters)
            if ret is not None:
                data["return"] = ret
            if market is not None:
                data["market"] = market
            return self._http.post_form(
                url,
                data=data,
                files=files,
                timeout=httpx.Timeout(timeout) if timeout else None,
            )

        try:
            resp = retry_sync(_do, self._recognition_policy())
        except httpx.RequestError as exc:
            elapsed = (monotonic() - started) * 1000.0
            _safe_emit(hook, AudDEvent(
                kind="exception", method="recognize", url=url, elapsed_ms=elapsed,
                extras={"error_type": type(exc).__name__},
            ))
            raise AudDConnectionError(str(exc), original=exc) from exc

        elapsed = (monotonic() - started) * 1000.0
        _safe_emit(hook, AudDEvent(
            kind="response", method="recognize", url=url,
            request_id=resp.request_id, http_status=resp.http_status, elapsed_ms=elapsed,
        ))
        return _decode_recognize(resp)

    def recognize_enterprise(
        self,
        source: Source,
        *,
        return_metadata: str | Iterable[str] | None = None,
        skip: int | None = None,
        every: int | None = None,
        limit: int | None = None,
        skip_first_seconds: int | None = None,
        use_timecode: bool | None = None,
        accurate_offsets: bool | None = None,
        timeout: float | None = None,
        extra_parameters: dict[str, str] | None = None,
    ) -> list[EnterpriseMatch]:
        """``extra_parameters`` carries additional form fields not covered by
        the typed kwargs. Typed kwargs win on collision.
        """
        reopen = prepare_source(source)
        extra = _build_enterprise_fields(
            _format_return(return_metadata),
            skip, every, limit, skip_first_seconds, use_timecode, accurate_offsets,
        )
        url = f"{ENTERPRISE_BASE}/"
        hook = self._on_event
        _safe_emit(hook, AudDEvent(kind="request", method="recognize", url=url))
        started = monotonic()

        def _do() -> Any:
            data, files = reopen()
            if extra_parameters:
                data.update(extra_parameters)
            data.update(extra)
            return self._enterprise_http.post_form(
                url,
                data=data,
                files=files,
                timeout=httpx.Timeout(timeout) if timeout else None,
            )

        try:
            resp = retry_sync(_do, self._recognition_policy())
        except httpx.RequestError as exc:
            elapsed = (monotonic() - started) * 1000.0
            _safe_emit(hook, AudDEvent(
                kind="exception", method="recognize", url=url, elapsed_ms=elapsed,
                extras={"error_type": type(exc).__name__},
            ))
            raise AudDConnectionError(str(exc), original=exc) from exc

        elapsed = (monotonic() - started) * 1000.0
        _safe_emit(hook, AudDEvent(
            kind="response", method="recognize", url=url,
            request_id=resp.request_id, http_status=resp.http_status, elapsed_ms=elapsed,
        ))
        return _decode_enterprise(resp)

    def close(self) -> None:
        self._http.close()
        self._enterprise_http.close()

    def __enter__(self) -> AudD:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class AsyncAudD(_BaseClient):
    """Async client for the AudD music recognition API.

    The ``api_token`` may be omitted; in that case the SDK reads
    ``AUDD_API_TOKEN`` from the environment (raising ``ValueError`` if neither
    is set).

    For cancellation, wrap calls in ``asyncio.wait_for(...)`` or call
    ``Task.cancel()`` — the SDK passes httpx's standard cancellation through.
    **Server-side metering still consumes credit even if the local call is
    cancelled**, so cancellation aborts only the local I/O wait, not the
    server-side cost.
    """

    def __init__(
        self,
        api_token: str | None = None,
        *,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        httpx_client: httpx.AsyncClient | None = None,
        on_event: OnEventHook | None = None,
    ) -> None:
        token = _resolve_token(api_token)
        super().__init__(
            token,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            on_event=on_event,
        )
        self._http = AsyncHTTPClient(token, httpx_client=httpx_client)
        self._enterprise_http = AsyncHTTPClient(
            token,
            timeouts=ENTERPRISE_TIMEOUTS,
            httpx_client=httpx_client,
        )
        self._streams: Any = None
        self._custom_catalog: Any = None
        self._advanced: Any = None

    def _http_layers(self) -> Iterable[Any]:
        return (self._http, self._enterprise_http)

    @property
    def streams(self) -> Any:
        if self._streams is None:
            from audd.streams import AsyncStreams

            self._streams = AsyncStreams(
                self._http, self._read_policy(), self._mutating_policy(),
                lambda: self.api_token,
            )
        return self._streams

    @property
    def custom_catalog(self) -> Any:
        if self._custom_catalog is None:
            from audd.custom_catalog import AsyncCustomCatalog

            self._custom_catalog = AsyncCustomCatalog(self._http, self._no_retry_policy())
        return self._custom_catalog

    @property
    def advanced(self) -> Any:
        if self._advanced is None:
            from audd.advanced import AsyncAdvanced

            self._advanced = AsyncAdvanced(self._http, self._recognition_policy())
        return self._advanced

    async def recognize(
        self,
        source: Source,
        *,
        return_metadata: str | Iterable[str] | None = None,
        market: str | None = None,
        timeout: float | None = None,
        extra_parameters: dict[str, str] | None = None,
    ) -> RecognitionResult | None:
        """Recognize a short clip. Wrap in ``asyncio.wait_for(...)`` for true
        cancellation (or call ``Task.cancel()``). **Server-side metering still
        consumes credit even if the local call is cancelled.**

        ``extra_parameters`` lets you pass additional form fields the typed
        kwargs don't cover. Typed kwargs win on collision.
        """
        reopen = prepare_source(source)
        ret = _format_return(return_metadata)
        url = f"{API_BASE}/"
        hook = self._on_event
        _safe_emit(hook, AudDEvent(kind="request", method="recognize", url=url))
        started = monotonic()

        async def _do() -> Any:
            data, files = reopen()
            if extra_parameters:
                data.update(extra_parameters)
            if ret is not None:
                data["return"] = ret
            if market is not None:
                data["market"] = market
            return await self._http.post_form(
                url,
                data=data,
                files=files,
                timeout=httpx.Timeout(timeout) if timeout else None,
            )

        try:
            resp = await retry_async(_do, self._recognition_policy())
        except httpx.RequestError as exc:
            elapsed = (monotonic() - started) * 1000.0
            _safe_emit(hook, AudDEvent(
                kind="exception", method="recognize", url=url, elapsed_ms=elapsed,
                extras={"error_type": type(exc).__name__},
            ))
            raise AudDConnectionError(str(exc), original=exc) from exc

        elapsed = (monotonic() - started) * 1000.0
        _safe_emit(hook, AudDEvent(
            kind="response", method="recognize", url=url,
            request_id=resp.request_id, http_status=resp.http_status, elapsed_ms=elapsed,
        ))
        return _decode_recognize(resp)

    async def recognize_enterprise(
        self,
        source: Source,
        *,
        return_metadata: str | Iterable[str] | None = None,
        skip: int | None = None,
        every: int | None = None,
        limit: int | None = None,
        skip_first_seconds: int | None = None,
        use_timecode: bool | None = None,
        accurate_offsets: bool | None = None,
        timeout: float | None = None,
        extra_parameters: dict[str, str] | None = None,
    ) -> list[EnterpriseMatch]:
        """``extra_parameters`` carries additional form fields not covered by
        the typed kwargs. Typed kwargs win on collision.
        """
        reopen = prepare_source(source)
        extra = _build_enterprise_fields(
            _format_return(return_metadata),
            skip, every, limit, skip_first_seconds, use_timecode, accurate_offsets,
        )
        url = f"{ENTERPRISE_BASE}/"
        hook = self._on_event
        _safe_emit(hook, AudDEvent(kind="request", method="recognize", url=url))
        started = monotonic()

        async def _do() -> Any:
            data, files = reopen()
            if extra_parameters:
                data.update(extra_parameters)
            data.update(extra)
            return await self._enterprise_http.post_form(
                url,
                data=data,
                files=files,
                timeout=httpx.Timeout(timeout) if timeout else None,
            )

        try:
            resp = await retry_async(_do, self._recognition_policy())
        except httpx.RequestError as exc:
            elapsed = (monotonic() - started) * 1000.0
            _safe_emit(hook, AudDEvent(
                kind="exception", method="recognize", url=url, elapsed_ms=elapsed,
                extras={"error_type": type(exc).__name__},
            ))
            raise AudDConnectionError(str(exc), original=exc) from exc

        elapsed = (monotonic() - started) * 1000.0
        _safe_emit(hook, AudDEvent(
            kind="response", method="recognize", url=url,
            request_id=resp.request_id, http_status=resp.http_status, elapsed_ms=elapsed,
        ))
        return _decode_enterprise(resp)

    async def aclose(self) -> None:
        await self._http.aclose()
        await self._enterprise_http.aclose()

    async def __aenter__(self) -> AsyncAudD:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()
