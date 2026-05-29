"""Pure helpers used by streams.* and callbacks. No HTTP, no SDK state."""
from __future__ import annotations

import hashlib
import json as _json
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from audd.errors import AudDInvalidRequestError, AudDSerializationError
from audd.models import StreamCallbackMatch, StreamCallbackNotification, StreamCallbackSong


class DuplicateReturnParameterError(AudDInvalidRequestError):
    """Raised when streams.set_callback_url is given both a URL containing
    ?return=... AND a return_metadata argument — conflicting intent."""

    def __init__(self) -> None:
        super().__init__(
            error_code=0,
            message=(
                "URL already contains a `return` query parameter; pass return_metadata=None "
                "or remove the parameter from the URL — refusing to silently overwrite."
            ),
            http_status=0,
            request_id=None,
        )


def derive_longpoll_category(api_token: str, radio_id: int) -> str:
    """Compute the 9-char longpoll category locally from token + radio_id.

    Formula (per docs.audd.io/streams.md): hex-MD5 of (hex-MD5 of api_token,
    concatenated with the radio_id rendered as a decimal string), truncated
    to the first 9 hex chars.
    """
    inner = hashlib.md5(api_token.encode("utf-8")).hexdigest()
    full = hashlib.md5((inner + str(radio_id)).encode("utf-8")).hexdigest()
    return full[:9]


def parse_callback(
    body: dict[str, Any] | bytes | str,
) -> tuple[StreamCallbackMatch | None, StreamCallbackNotification | None]:
    """Parse a callback POST body into a typed match or notification.

    Recognition callbacks have an outer ``result`` block; notification
    callbacks have a ``notification`` block; the discrimination is by-key.
    On success exactly one of ``(match, notification)`` is non-None.

    ``body`` may be the parsed JSON dict, raw bytes, or a JSON string. Use
    :py:func:`audd.streams.Streams.handle_callback` (or ``handle_callback``
    on :class:`AsyncStreams`) when you have a framework request object.

    Raises:
        AudDSerializationError: body isn't valid JSON, or has neither
            ``result`` nor ``notification``, or a ``result`` block is empty.
    """
    payload = _coerce_to_dict(body)

    if "notification" in payload:
        try:
            notif = StreamCallbackNotification.model_validate(payload["notification"])
        except Exception as exc:  # pydantic.ValidationError, etc.
            raise AudDSerializationError(
                f"callback notification: {exc}",
                raw_text=_repr_body(body),
            ) from exc
        time_val = payload.get("time")
        if isinstance(time_val, int):
            notif.time = time_val
        notif.raw_response = payload
        return None, notif

    if "result" in payload:
        match = _parse_match(payload, body)
        return match, None

    raise AudDSerializationError(
        "callback body has neither result nor notification",
        raw_text=_repr_body(body),
    )


def _coerce_to_dict(body: dict[str, Any] | bytes | str) -> dict[str, Any]:
    if isinstance(body, dict):
        return body
    raw_text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    try:
        parsed = _json.loads(raw_text)
    except _json.JSONDecodeError as exc:
        raise AudDSerializationError(
            f"callback body is not valid JSON: {exc}",
            raw_text=raw_text,
        ) from exc
    if not isinstance(parsed, dict):
        raise AudDSerializationError(
            "callback body is not a JSON object",
            raw_text=raw_text,
        )
    return parsed


def _parse_match(
    payload: dict[str, Any],
    original_body: dict[str, Any] | bytes | str,
) -> StreamCallbackMatch:
    inner = payload.get("result") or {}
    if not isinstance(inner, dict):
        raise AudDSerializationError(
            "callback result block is not an object",
            raw_text=_repr_body(original_body),
        )
    results_raw = inner.get("results")
    if not isinstance(results_raw, list) or not results_raw:
        raise AudDSerializationError(
            "callback result.results is empty",
            raw_text=_repr_body(original_body),
        )
    try:
        songs = [StreamCallbackSong.model_validate(s) for s in results_raw]
    except Exception as exc:
        raise AudDSerializationError(
            f"callback result.results entry: {exc}",
            raw_text=_repr_body(original_body),
        ) from exc
    # Build the flat StreamCallbackMatch — model_validate on a copy of the
    # inner dict carries top-level extras (radio_id, timestamp, play_length
    # known; everything else lands in model_extra).
    match_data = {k: v for k, v in inner.items() if k != "results"}
    match_data["song"] = songs[0]
    match_data["alternatives"] = songs[1:]
    try:
        match = StreamCallbackMatch.model_validate(match_data)
    except Exception as exc:
        raise AudDSerializationError(
            f"callback result: {exc}",
            raw_text=_repr_body(original_body),
        ) from exc
    match.raw_response = payload
    return match


def _repr_body(body: dict[str, Any] | bytes | str) -> str:
    """Best-effort string representation of the original body for error diagnostics."""
    if isinstance(body, str):
        return body
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    try:
        return _json.dumps(body)
    except Exception:
        return repr(body)


def add_return_to_url(
    url: str,
    return_metadata: str | list[str] | None,
) -> str:
    """Append `?return=<metadata>` (or merge as `&return=`) to the callback URL.

    If `return_metadata` is None, return the URL unchanged.
    If the URL already has a `return` query parameter, raise to avoid silent overwrite.
    """
    if return_metadata is None:
        return url

    metadata = (
        ",".join(return_metadata)
        if isinstance(return_metadata, list)
        else return_metadata
    )

    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    if "return" in qs:
        raise DuplicateReturnParameterError()
    qs["return"] = [metadata]
    new_query = urlencode([(k, v) for k, vs in qs.items() for v in vs])
    return urlunparse(parsed._replace(query=new_query))
