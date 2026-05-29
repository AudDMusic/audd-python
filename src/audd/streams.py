"""Streams namespace — set_callback_url, addStream, longpoll with preflight, etc."""
from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import AsyncIterator, Awaitable, Iterator
from queue import Empty, Queue
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
from audd.models import (
    Stream,
    StreamCallbackMatch,
    StreamCallbackNotification,
    _coerce_model_list,
)

API_BASE = "https://api.audd.io"
LONGPOLL_URL = f"{API_BASE}/longpoll/"

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
    ``errors`` is single-shot — the first error terminates the subscription.

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
        """Stop the background poller. Idempotent."""
        if self._closed.is_set():
            return
        self._closed.set()
        self._stop.set()
        # Don't join — the HTTP read may be blocked in httpx for up to ``timeout``
        # seconds. Daemon thread cleans up at process exit. Best-effort short
        # join to give in-flight responses a chance to flush.
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


def _classify_response(resp: Any) -> tuple[
    StreamCallbackMatch | None,
    StreamCallbackNotification | None,
    Exception | None,
    int | None,
]:
    """Translate a longpoll HTTP response into one of:

    * ``(match, None, None, since)`` — match dispatched
    * ``(None, notif, None, since)`` — notification dispatched
    * ``(None, None, None, since)`` — pure-timeout payload, just advance since_time
    * ``(None, None, exc, None)`` — terminal error

    The returned ``since`` is the new ``since_time`` to use on the next poll
    (None if the body had no usable timestamp).
    """
    if resp.http_status >= _HTTP_CLIENT_ERROR_FLOOR:
        return None, None, AudDServerError(
            error_code=0,
            message=f"Longpoll endpoint returned HTTP {resp.http_status}",
            http_status=resp.http_status,
            request_id=resp.request_id,
            raw_response=resp.json_body if resp.json_body is not None else resp.raw_text,
        ), None

    body = resp.json_body
    if not isinstance(body, dict):
        return None, None, AudDSerializationError(
            "Longpoll response was not a JSON object",
            raw_text=resp.raw_text,
        ), None

    ts = body.get("timestamp") if isinstance(body.get("timestamp"), int) else None

    # Drop pure-timeout payloads silently — they only update since_time.
    if "result" not in body and "notification" not in body:
        return None, None, None, ts

    try:
        match, notif = parse_callback(body)
    except AudDSerializationError as exc:
        return None, None, exc, None
    return match, notif, None, ts


def _run_longpoll_sync(
    poll: _SyncLongpollPoll,
    fetch: Any,
    category: str,
    since_time: int | None,
    timeout: int,
) -> None:
    cur_since = since_time
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

            match, notif, err, new_since = _classify_response(resp)
            if err is not None:
                poll._errors.put(err)
                return
            if match is not None:
                poll._matches.put(match)
            elif notif is not None:
                poll._notifications.put(notif)
            if new_since is not None:
                cur_since = new_since
    finally:
        poll._terminated.set()


class _AsyncLongpollPoll:
    """Async long-poll subscription handle.

    Three async iterators surface output: ``matches``, ``notifications``,
    ``errors``. ``errors`` is single-shot — the first error terminates the
    subscription.

    Supports the async context-manager protocol; ``aclose()`` is idempotent.
    """

    def __init__(self) -> None:
        # Bounded queues give natural backpressure: the poll task awaits put()
        # when the consumer falls behind, so cancellation arrives at a yield
        # point rather than spinning the event loop.
        self._matches: asyncio.Queue[StreamCallbackMatch] = asyncio.Queue(maxsize=1)
        self._notifications: asyncio.Queue[StreamCallbackNotification] = asyncio.Queue(maxsize=1)
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

            match, notif, err, new_since = _classify_response(resp)
            if err is not None:
                await poll._errors.put(err)
                return
            if match is not None:
                await poll._matches.put(match)
            elif notif is not None:
                await poll._notifications.put(notif)
            if new_since is not None:
                cur_since = new_since
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
        thread. ``errors`` is single-shot; the first error terminates the
        subscription. Use the handle as a context manager for clean shutdown:

        .. code-block:: python

            with audd.streams.longpoll(radio_id=42) as poll:
                for m in poll.matches:
                    print(m.song.artist, m.song.title)

        On entry: preflights ``getCallbackUrl`` unless ``skip_callback_check=True``.
        """
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

        def fetch(params: dict[str, Any]) -> Any:
            def _do() -> Any:
                return self._http.get(LONGPOLL_URL, params=params)
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
        ``poll.notifications``, ``poll.errors``. Use it as an async context
        manager for clean shutdown:

        .. code-block:: python

            async with await audd.streams.longpoll(radio_id=42) as poll:
                async for m in poll.matches:
                    print(m.song.artist, m.song.title)

        On entry: preflights ``getCallbackUrl`` unless ``skip_callback_check=True``.
        """
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

        async def fetch(params: dict[str, Any]) -> Any:
            async def _do() -> Any:
                return await self._http.get(LONGPOLL_URL, params=params)
            return await retry_async(_do, self._read)

        poll = _AsyncLongpollPoll()
        poll._start(_run_longpoll_async(poll, fetch, category, since_time, timeout))
        return poll
