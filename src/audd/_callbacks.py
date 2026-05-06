"""Pure helpers used by streams.* and callbacks. No HTTP, no SDK state."""
from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from audd.errors import AudDInvalidRequestError
from audd.models import StreamCallbackPayload


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


def parse_callback(body: dict[str, Any]) -> StreamCallbackPayload:
    """Parse a callback POST body into a typed payload."""
    return StreamCallbackPayload.parse(body)


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
