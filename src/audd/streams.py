"""Streams namespace — set_callback_url, addStream, longpoll with preflight, etc."""
from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections.abc import AsyncIterator, Awaitable, Iterator
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any

import httpx

from audd._callbacks import (
    add_return_to_url,
    derive_longpoll_category,
    parse_callback,
)
from audd._http import DEFAULT_TIMEOUTS, AsyncHTTPClient, HTTPClient
from audd._retry import RetryPolicy, retry_async, retry_sync
from audd.errors import (
    AudDAPIError,
    AudDConnectionError,
    AudDInvalidRequestError,
    AudDSerializationError,
    AudDServerError,
    raise_from_error_response,
)
from audd.models import (
    Stream,
    StreamCallbackMatch,
    StreamCallbackNotification,
    _coerce_int,
    _coerce_model_list,
)

API_BASE = "https://api.audd.io"
LONGPOLL_URL = f"{API_BASE}/longpoll/"

_log = logging.getLogger("audd")

# The server holds each longpoll request open for 1-120 seconds.
_LONGPOLL_TIMEOUT_MIN = 1
_LONGPOLL_TIMEOUT_MAX = 120
# Seconds the HTTP read waits beyond the poll's own ``timeout``.
_LONGPOLL_READ_MARGIN = 10.0
# Pause before the next request when a response brought no event and no new
# timestamp: starts at 1 s and doubles up to 30 s.
_IDLE_BACKOFF_INITIAL = 1.0
_IDLE_BACKOFF_MAX = 30.0

# Server returns error #19 from getCallbackUrl when no callback URL is configured.
# Catch-all "Internal error" code; we treat it specifically here as the
# no-callback-set signal per docs/captured behavior.
_NO_CALLBACK_ERROR_CODE = 19
_HTTP_CLIENT_ERROR_FLOOR = 400

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


# ---------------------------------------------------------------------------
# Callback body extraction — duck-typed across Flask / FastAPI / Django / aiohttp.
# ---------------------------------------------------------------------------


def _extract_body_sync(request: Any) -> bytes:
    """Extract raw bytes from a sync framework request.

    Supports:
    * Django: ``request.body`` (bytes attribute)
    * Flask:  ``request.get_data()`` (returns bytes)
    * Anything with ``.read()`` returning bytes
    * Raw ``bytes`` / ``str``
    """
    if isinstance(request, (bytes, bytearray)):
        return bytes(request)
    if isinstance(request, str):
        return request.encode("utf-8")

    # Flask: get_data() (sync). Prefer over ``body`` because Flask's
    # request.body is the WSGI input stream, not the resolved bytes.
    get_data = getattr(request, "get_data", None)
    if callable(get_data) and not inspect.iscoroutinefunction(get_data):
        data = get_data()
        return data if isinstance(data, bytes) else bytes(data)

    # Django and similar: ``body`` attribute holds the resolved bytes.
    body = getattr(request, "body", None)
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)

    # Last-resort: a file-like with .read().
    read = getattr(request, "read", None)
    if callable(read) and not inspect.iscoroutinefunction(read):
        data = read()
        return data if isinstance(data, bytes) else bytes(data, "utf-8")

    raise AudDSerializationError(
        "Could not extract bytes from request; pass raw bytes/str, or a request "
        "object with `body`, `get_data()`, or `read()`",
    )


async def _maybe_call_body(request: Any) -> bytes | None:
    """Try the ``body`` attribute (callable or bytes). Return None if absent/unusable."""
    body_fn = getattr(request, "body", None)
    if isinstance(body_fn, (bytes, bytearray)):
        # Django async: body is bytes, not a method.
        return bytes(body_fn)
    if not callable(body_fn):
        return None
    if inspect.iscoroutinefunction(body_fn):
        result: Any = await body_fn()
    else:
        result = body_fn()
        if inspect.isawaitable(result):
            result = await result
    if isinstance(result, (bytes, bytearray)):
        return bytes(result)
    return None


async def _maybe_call_read(request: Any) -> bytes | None:
    """Try the ``read`` callable (sync or async). Return None if absent."""
    read = getattr(request, "read", None)
    if not callable(read):
        return None
    if inspect.iscoroutinefunction(read):
        result: Any = await read()
    else:
        result = read()
        if inspect.isawaitable(result):
            result = await result
    if isinstance(result, (bytes, bytearray)):
        return bytes(result)
    return None


async def _extract_body_async(request: Any) -> bytes:
    """Extract raw bytes from an async framework request.

    Supports:
    * Starlette/FastAPI: ``await request.body()``
    * aiohttp:           ``await request.read()``
    * Django (async views): ``request.body`` attribute
    * Plus everything :py:func:`_extract_body_sync` accepts.
    """
    if isinstance(request, (bytes, bytearray)):
        return bytes(request)
    if isinstance(request, str):
        return request.encode("utf-8")

    body = await _maybe_call_body(request)
    if body is not None:
        return body

    body = await _maybe_call_read(request)
    if body is not None:
        return body

    # Sync Flask request inside an async handler.
    get_data = getattr(request, "get_data", None)
    if callable(get_data) and not inspect.iscoroutinefunction(get_data):
        data = get_data()
        return data if isinstance(data, bytes) else bytes(data)

    raise AudDSerializationError(
        "Could not extract bytes from async request; pass raw bytes/str, or a "
        "request object with `body`/`body()`, `read()`, or `get_data()`",
    )


# ---------------------------------------------------------------------------
# Longpoll handles — typed iterators for matches / notifications / errors.
# ---------------------------------------------------------------------------


class _SyncLongpollPoll:
    """Sync long-poll subscription handle.

    Three iterators surface output: ``matches``, ``notifications``, ``errors``.
    ``errors`` is single-shot — the first error terminates the subscription,
    and ``matches`` / ``notifications`` then end once drained.

    Supports the context-manager protocol; ``close()`` is idempotent.
    """

    def __init__(
        self,
        fetch: Any,
        category: str,
        since_time: int | None,
        timeout: int,
    ) -> None:
        self._matches: Queue[StreamCallbackMatch] = Queue()
        self._notifications: Queue[StreamCallbackNotification] = Queue()
        self._errors: Queue[Exception] = Queue()
        self._stop = threading.Event()
        self._closed = threading.Event()
        self._terminated = threading.Event()
        self._thread = threading.Thread(
            target=_run_longpoll_sync,
            args=(self, fetch, category, since_time, timeout),
            name="audd-longpoll",
            daemon=True,
        )
        self._thread.start()

    @property
    def matches(self) -> Iterator[StreamCallbackMatch]:
        """Iterate recognition matches. Yields until the poll terminates."""
        return self._iter_queue(self._matches)

    @property
    def notifications(self) -> Iterator[StreamCallbackNotification]:
        """Iterate stream-lifecycle events. Yields until the poll terminates."""
        return self._iter_queue(self._notifications)

    @property
    def errors(self) -> Iterator[Exception]:
        """Iterate terminal errors (at most one before the poll closes)."""
        return self._iter_queue(self._errors)

    def _iter_queue(self, q: Queue[Any]) -> Iterator[Any]:
        while True:
            try:
                item = q.get(timeout=0.1)
            except Empty:
                if self._terminated.is_set() and q.empty():
                    return
                continue
            yield item

    def close(self) -> None:
        """Stop the background poller and end the iterators. Idempotent.

        No request is sent after ``close()``, and the response to a request
        still waiting on the server is discarded.
        """
        if self._closed.is_set():
            return
        self._closed.set()
        self._stop.set()
        # The iterators end at this point (after draining what's queued); the poller
        # thread checks ``_stop`` before delivering a response.
        self._terminated.set()
        # httpx's sync API can't abort a blocked read from another thread, so
        # don't wait out a request the server is still holding: the daemon
        # thread exits as soon as it returns. Short join for the common case.
        self._thread.join(timeout=0.5)

    def __enter__(self) -> _SyncLongpollPoll:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _build_longpoll_params(
    category: str, timeout: int, since_time: int | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"category": category, "timeout": str(timeout)}
    if since_time is not None:
        params["since_time"] = str(since_time)
    return params


def _validate_longpoll_timeout(timeout: Any) -> int:
    """Check the caller's ``timeout`` against the server's accepted range."""
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or not _LONGPOLL_TIMEOUT_MIN <= timeout <= _LONGPOLL_TIMEOUT_MAX
    ):
        raise ValueError(
            "longpoll timeout must be a whole number of seconds from "
            f"{_LONGPOLL_TIMEOUT_MIN} to {_LONGPOLL_TIMEOUT_MAX}, got {timeout!r}",
        )
    return timeout


def _longpoll_request_timeout(
    timeout: int, base: httpx.Timeout = DEFAULT_TIMEOUTS,
) -> httpx.Timeout:
    """httpx timeout for one longpoll GET: the read outlasts the server's hold."""
    return httpx.Timeout(
        connect=base.connect,
        read=timeout + _LONGPOLL_READ_MARGIN,
        write=base.write,
        pool=base.pool,
    )


_LongpollItem = StreamCallbackMatch | StreamCallbackNotification


@dataclass
class _LongpollOutcome:
    """What one longpoll response yields.

    ``items``: parsed matches and notifications, in server order.
    ``since``: the ``since_time`` for the next request (None: keep the current one).
    ``error``: set when the subscription has to end.
    """

    items: list[_LongpollItem] = field(default_factory=list)
    since: int | None = None
    error: Exception | None = None


def _timestamp_ms(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return _coerce_int(value)


def _parse_callback_body(data: Any) -> _LongpollItem | None:
    """Parse a callback body (object or JSON text). None when it can't be parsed."""
    if isinstance(data, bytearray):
        data = bytes(data)
    if not isinstance(data, (dict, str, bytes)):
        return None
    try:
        match, notif = parse_callback(data)
    except AudDSerializationError:
        return None
    return match if match is not None else notif


def _classify_events(events: list[Any], since: int | None) -> _LongpollOutcome:
    """``{"events": [{"timestamp", "category", "data"}, ...]}``: each ``data``
    is a callback body. Events that can't be parsed are skipped."""
    out = _LongpollOutcome()
    for event in events:
        if not isinstance(event, dict):
            continue
        ts = _timestamp_ms(event.get("timestamp"))
        if ts is not None:
            if since is not None and ts <= since:
                # The server only returns events newer than since_time;
                # anything older was delivered by an earlier response.
                continue
            out.since = ts if out.since is None else max(out.since, ts)
        item = _parse_callback_body(event.get("data"))
        if item is None:
            _log.debug("audd longpoll: skipped an unparseable event (timestamp %s)", ts)
            continue
        out.items.append(item)
    return out


def _response_error(resp: Any) -> AudDAPIError | None:
    """The error that ends the subscription, if this response carries one."""
    body = resp.json_body
    if resp.http_status >= _HTTP_CLIENT_ERROR_FLOOR:
        message = f"Longpoll endpoint returned HTTP {resp.http_status}"
        if isinstance(body, dict) and isinstance(body.get("error"), str):
            message = f"{message}: {body['error']}"
        return AudDServerError(
            error_code=0,
            message=message,
            http_status=resp.http_status,
            request_id=resp.request_id,
            raw_response=body if body is not None else resp.raw_text,
        )
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if body.get("status") == "error" or isinstance(error, dict):
        try:
            raise_from_error_response(
                body, http_status=resp.http_status, request_id=resp.request_id,
            )
        except AudDAPIError as exc:
            return exc
    if error is None:
        return None
    # ``{"error": "..."}`` with HTTP 200: the same request gets the same
    # error again, so it ends the subscription.
    return AudDInvalidRequestError(
        error_code=0,
        message=str(error),
        http_status=resp.http_status,
        request_id=resp.request_id,
        raw_response=body,
    )


def _classify_response(resp: Any, since: int | None = None) -> _LongpollOutcome:
    """Translate one longpoll HTTP response.

    The server answers with an events body, a timeout body
    (``{"timeout": ..., "timestamp": ms}``) or an error body
    (``{"error": "..."}``, sent with HTTP 200). A body with ``result`` or
    ``notification`` at the top level (plus an optional ``timestamp``) is
    read as a single callback body. An empty, non-JSON or unknown body
    yields nothing, and the loop backs off before the next request.
    """
    error = _response_error(resp)
    if error is not None:
        return _LongpollOutcome(error=error)

    body = resp.json_body
    if not isinstance(body, dict):
        return _LongpollOutcome()

    events = body.get("events")
    if isinstance(events, list):
        return _classify_events(events, since)

    out = _LongpollOutcome(since=_timestamp_ms(body.get("timestamp")))
    if "result" in body or "notification" in body:
        item = _parse_callback_body(body)
        if item is not None:
            out.items.append(item)
        else:
            _log.debug("audd longpoll: skipped a response body that could not be parsed")
    return out


class _IdleBackoff:
    """Pause before the next request after a response that brought nothing new."""

    def __init__(self) -> None:
        self._next = _IDLE_BACKOFF_INITIAL

    def delay_after(self, outcome: _LongpollOutcome, since: int | None) -> float:
        """0 when the response delivered an item or a new timestamp; otherwise
        the next pause, doubling from 1 s up to 30 s."""
        if outcome.items or (outcome.since is not None and outcome.since != since):
            self._next = _IDLE_BACKOFF_INITIAL
            return 0.0
        delay = self._next
        self._next = min(delay * 2, _IDLE_BACKOFF_MAX)
        _log.warning(
            "audd longpoll: the response had no events and no new timestamp; "
            "next request in %.0f s",
            delay,
        )
        return delay


def _run_longpoll_sync(
    poll: _SyncLongpollPoll,
    fetch: Any,
    category: str,
    since_time: int | None,
    timeout: int,
) -> None:
    cur_since = since_time
    idle = _IdleBackoff()
    try:
        while not poll._stop.is_set():
            params = _build_longpoll_params(category, timeout, cur_since)

            try:
                resp = fetch(params)
            except httpx.RequestError as exc:
                poll._errors.put(AudDConnectionError(str(exc), original=exc))
                return
            except Exception as exc:
                # Surface the error on the errors channel — broad catch is intentional.
                poll._errors.put(exc)
                return
            if poll._stop.is_set():
                return  # closed while the request was waiting

            outcome = _classify_response(resp, cur_since)
            if outcome.error is not None:
                poll._errors.put(outcome.error)
                return
            for item in outcome.items:
                if isinstance(item, StreamCallbackMatch):
                    poll._matches.put(item)
                else:
                    poll._notifications.put(item)
            delay = idle.delay_after(outcome, cur_since)
            if outcome.since is not None:
                cur_since = outcome.since
            if delay and poll._stop.wait(delay):
                return
    finally:
        poll._terminated.set()


class _AsyncLongpollPoll:
    """Async long-poll subscription handle.

    Three async iterators surface output: ``matches``, ``notifications``,
    ``errors``. ``errors`` is single-shot — the first error terminates the
    subscription, and ``matches`` / ``notifications`` then end once drained.

    Supports the async context-manager protocol; ``aclose()`` is idempotent
    and cancels a request that is still waiting on the server.
    """

    def __init__(self) -> None:
        # Unbounded queues, matching the sync path: the producer must never
        # block on put(). With bounded queues, a consumer that only drains
        # ``matches`` would stall the poll loop after two undrained lifecycle
        # notifications, silently freezing the whole subscription.
        self._matches: asyncio.Queue[StreamCallbackMatch] = asyncio.Queue()
        self._notifications: asyncio.Queue[StreamCallbackNotification] = asyncio.Queue()
        # Errors is single-shot — never blocks the producer.
        self._errors: asyncio.Queue[Exception] = asyncio.Queue()
        self._stop = asyncio.Event()
        self._terminated = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def _start(self, coro: Awaitable[None]) -> None:
        self._task = asyncio.ensure_future(coro)

    @property
    def matches(self) -> AsyncIterator[StreamCallbackMatch]:
        return self._iter_queue(self._matches)

    @property
    def notifications(self) -> AsyncIterator[StreamCallbackNotification]:
        return self._iter_queue(self._notifications)

    @property
    def errors(self) -> AsyncIterator[Exception]:
        return self._iter_queue(self._errors)

    async def _iter_queue(self, q: asyncio.Queue[Any]) -> AsyncIterator[Any]:
        while True:
            getter = asyncio.ensure_future(q.get())
            terminated = asyncio.ensure_future(self._terminated.wait())
            done, _ = await asyncio.wait(
                {getter, terminated}, return_when=asyncio.FIRST_COMPLETED,
            )
            if getter in done:
                terminated.cancel()
                yield getter.result()
                continue
            # Terminated. Drain anything still on the queue, then stop.
            getter.cancel()
            while not q.empty():
                yield q.get_nowait()
            return

    async def aclose(self) -> None:
        """Stop the background poller. Idempotent."""
        self._stop.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                # Shutdown path — never surface fetch errors here.
                pass
        self._terminated.set()

    async def __aenter__(self) -> _AsyncLongpollPoll:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()


async def _run_longpoll_async(
    poll: _AsyncLongpollPoll,
    fetch: Any,
    category: str,
    since_time: int | None,
    timeout: int,
) -> None:
    cur_since = since_time
    idle = _IdleBackoff()
    try:
        while not poll._stop.is_set():
            params = _build_longpoll_params(category, timeout, cur_since)

            try:
                resp = await fetch(params)
            except asyncio.CancelledError:
                raise
            except httpx.RequestError as exc:
                await poll._errors.put(AudDConnectionError(str(exc), original=exc))
                return
            except Exception as exc:
                # Surface the error on the errors channel — broad catch is intentional.
                await poll._errors.put(exc)
                return
            if poll._stop.is_set():
                return

            outcome = _classify_response(resp, cur_since)
            if outcome.error is not None:
                await poll._errors.put(outcome.error)
                return
            for item in outcome.items:
                if isinstance(item, StreamCallbackMatch):
                    await poll._matches.put(item)
                else:
                    await poll._notifications.put(item)
            delay = idle.delay_after(outcome, cur_since)
            if outcome.since is not None:
                cur_since = outcome.since
            # Always yield at least once per cycle: puts on unbounded queues
            # never suspend, and fetch may resolve without touching the loop.
            await asyncio.sleep(delay)
    finally:
        poll._terminated.set()


# ---------------------------------------------------------------------------
# Streams namespaces.
# ---------------------------------------------------------------------------


class _StreamsBase:
    def __init__(self, token_getter: Any) -> None:
        # token_getter is a zero-arg callable returning the current token,
        # so derive_longpoll_category honors set_api_token rotations.
        self._token_getter = token_getter

    def derive_longpoll_category(self, radio_id: int) -> str:
        """Compute MD5(MD5(api_token)+str(radio_id))[:9] locally."""
        return derive_longpoll_category(self._token_getter(), radio_id)

    def _resolve_longpoll_category(
        self, category: str | None, radio_id: int | None,
    ) -> str:
        """Resolve the longpoll category from one-of (category, radio_id).

        Raises ``TypeError`` if both or neither are provided.
        """
        if category is not None and radio_id is not None:
            raise TypeError(
                "longpoll() takes exactly one of `category` or `radio_id`, not both",
            )
        if category is None and radio_id is None:
            raise TypeError(
                "longpoll() requires one of `category` or `radio_id`",
            )
        if radio_id is not None:
            return self.derive_longpoll_category(radio_id)
        # category is not None per the checks above; keep mypy happy.
        assert category is not None
        return category

    @staticmethod
    def parse_callback(
        body: dict[str, Any] | bytes | str,
    ) -> tuple[StreamCallbackMatch | None, StreamCallbackNotification | None]:
        """Parse a callback POST body into ``(match, notification)``.

        Exactly one of the two is non-None on success.
        """
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
        extra_parameters: dict[str, str] | None = None,
    ) -> None:
        """``extra_parameters`` are additional form fields sent on the
        setCallbackUrl call. Typed fields (``url``) win on collision.
        """
        url = add_return_to_url(url, return_metadata)
        data: dict[str, Any] = dict(extra_parameters or {})
        data["url"] = url
        self._post("setCallbackUrl", data, self._mutating)

    def get_callback_url(self) -> str:
        result = self._post("getCallbackUrl", {}, self._read)
        return str(result)

    def add(
        self,
        url: str,
        radio_id: int,
        *,
        callbacks: str | None = None,
        extra_parameters: dict[str, str] | None = None,
    ) -> None:
        """``extra_parameters`` are additional form fields sent on the
        addStream call. Typed fields (``url``, ``radio_id``, ``callbacks``)
        win on collision.
        """
        data: dict[str, Any] = dict(extra_parameters or {})
        data["url"] = url
        data["radio_id"] = str(radio_id)
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
        result = self._post("getStreams", {}, self._read)
        return _coerce_model_list(result, Stream)

    def handle_callback(
        self, request: Any,
    ) -> tuple[StreamCallbackMatch | None, StreamCallbackNotification | None]:
        """Read + parse a callback from a sync framework request.

        Accepts Flask (``request.get_data()``), Django (``request.body``),
        anything with ``.read()``, or raw ``bytes`` / ``str``. Returns
        ``(match, notification)`` — exactly one non-None on success.

        Raises:
            AudDSerializationError: body is malformed or has neither a
                ``result`` nor ``notification`` block.
        """
        return parse_callback(_extract_body_sync(request))

    def longpoll(
        self,
        category: str | None = None,
        *,
        radio_id: int | None = None,
        since_time: int | None = None,
        timeout: int = 50,
        skip_callback_check: bool = False,
    ) -> _SyncLongpollPoll:
        """Start a long-poll subscription. Returns a poll handle.

        Pass either ``category`` (a pre-derived 9-char string — useful for the
        share-without-token use case) or the keyword-only ``radio_id`` (the SDK
        derives the category locally from the configured api_token). Exactly
        one is required.

        The handle exposes three iterators — ``poll.matches``,
        ``poll.notifications``, ``poll.errors`` — populated by a background
        thread. Each request waits up to ``timeout`` seconds (1-120) for new
        events; the next one asks only for events newer than the newest
        timestamp seen so far. Network failures and HTTP 5xx are retried with
        the client's ``max_retries`` / ``backoff_factor``.
        ``errors`` is single-shot: the first error terminates the
        subscription, and ``matches`` / ``notifications`` then end. Use the
        handle as a context manager for clean shutdown:

        .. code-block:: python

            with audd.streams.longpoll(radio_id=42) as poll:
                for m in poll.matches:
                    print(m.song.artist, m.song.title)
                for err in poll.errors:
                    raise err

        On entry: preflights ``getCallbackUrl`` unless ``skip_callback_check=True``.

        Raises:
            ValueError: ``timeout`` is outside 1-120.
        """
        timeout = _validate_longpoll_timeout(timeout)
        category = self._resolve_longpoll_category(category, radio_id)

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

        request_timeout = _longpoll_request_timeout(timeout)

        def fetch(params: dict[str, Any]) -> Any:
            def _do() -> Any:
                # The longpoll endpoint authorizes by category alone and does
                # not accept api_token — never send it here.
                return self._http.get(
                    LONGPOLL_URL, params=params, timeout=request_timeout, send_token=False,
                )
            return retry_sync(_do, self._read)

        return _SyncLongpollPoll(fetch, category, since_time, timeout)


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
        extra_parameters: dict[str, str] | None = None,
    ) -> None:
        """``extra_parameters`` are additional form fields sent on the
        setCallbackUrl call. Typed fields (``url``) win on collision.
        """
        url = add_return_to_url(url, return_metadata)
        data: dict[str, Any] = dict(extra_parameters or {})
        data["url"] = url
        await self._post("setCallbackUrl", data, self._mutating)

    async def get_callback_url(self) -> str:
        return str(await self._post("getCallbackUrl", {}, self._read))

    async def add(
        self,
        url: str,
        radio_id: int,
        *,
        callbacks: str | None = None,
        extra_parameters: dict[str, str] | None = None,
    ) -> None:
        """``extra_parameters`` are additional form fields sent on the
        addStream call. Typed fields (``url``, ``radio_id``, ``callbacks``)
        win on collision.
        """
        data: dict[str, Any] = dict(extra_parameters or {})
        data["url"] = url
        data["radio_id"] = str(radio_id)
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
        result = await self._post("getStreams", {}, self._read)
        return _coerce_model_list(result, Stream)

    async def handle_callback(
        self, request: Any,
    ) -> tuple[StreamCallbackMatch | None, StreamCallbackNotification | None]:
        """Read + parse a callback from an async framework request.

        Accepts FastAPI/Starlette (``await request.body()``), aiohttp
        (``await request.read()``), Django (``request.body`` attribute), or
        raw ``bytes`` / ``str``. Returns ``(match, notification)`` — exactly
        one non-None on success.

        Raises:
            AudDSerializationError: body is malformed or has neither a
                ``result`` nor ``notification`` block.
        """
        return parse_callback(await _extract_body_async(request))

    async def longpoll(
        self,
        category: str | None = None,
        *,
        radio_id: int | None = None,
        since_time: int | None = None,
        timeout: int = 50,
        skip_callback_check: bool = False,
    ) -> _AsyncLongpollPoll:
        """Start an async long-poll subscription. Returns a poll handle.

        Pass either ``category`` (a pre-derived 9-char string — useful for the
        share-without-token use case) or the keyword-only ``radio_id`` (the SDK
        derives the category locally from the configured api_token). Exactly
        one is required.

        The handle exposes three async iterators — ``poll.matches``,
        ``poll.notifications``, ``poll.errors`` — and polls the same way as
        :meth:`Streams.longpoll`. Use it as an async context manager for clean
        shutdown:

        .. code-block:: python

            async with await audd.streams.longpoll(radio_id=42) as poll:
                async for m in poll.matches:
                    print(m.song.artist, m.song.title)
                async for err in poll.errors:
                    raise err

        On entry: preflights ``getCallbackUrl`` unless ``skip_callback_check=True``.

        Raises:
            ValueError: ``timeout`` is outside 1-120.
        """
        timeout = _validate_longpoll_timeout(timeout)
        category = self._resolve_longpoll_category(category, radio_id)

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

        request_timeout = _longpoll_request_timeout(timeout)

        async def fetch(params: dict[str, Any]) -> Any:
            async def _do() -> Any:
                # The longpoll endpoint authorizes by category alone and does
                # not accept api_token — never send it here.
                return await self._http.get(
                    LONGPOLL_URL, params=params, timeout=request_timeout, send_token=False,
                )
            return await retry_async(_do, self._read)

        poll = _AsyncLongpollPoll()
        poll._start(_run_longpoll_async(poll, fetch, category, since_time, timeout))
        return poll
