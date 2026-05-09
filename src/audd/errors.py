"""Exception hierarchy for AudD API errors."""
from __future__ import annotations

from typing import Any


class AudDError(Exception):
    """Base for everything raised by this SDK."""


class AudDAPIError(AudDError):
    """Server returned status=error. Carries the AudD error code + the full echo."""

    def __init__(
        self,
        error_code: int,
        message: str,
        *,
        http_status: int,
        request_id: str | None,
        requested_params: dict[str, Any] | None = None,
        request_method: str | None = None,
        branded_message: str | None = None,
        raw_response: Any = None,
    ) -> None:
        self.error_code = error_code
        self.message = message
        self.http_status = http_status
        self.request_id = request_id
        self.requested_params = requested_params or {}
        self.request_method = request_method
        self.branded_message = branded_message
        self.raw_response = raw_response
        super().__init__(f"[#{error_code}] {message}")


class AudDAuthenticationError(AudDAPIError):
    """900 / 901 / 903 — token is the problem."""


class AudDQuotaError(AudDAPIError):
    """902 — quota / per-copy limit reached."""


class AudDSubscriptionError(AudDAPIError):
    """904 / 905 — endpoint not available with this token."""


class AudDCustomCatalogAccessError(AudDSubscriptionError):
    """904 raised specifically from custom_catalog.* — overridden message."""

    def __init__(self, error_code: int, server_message: str, **kwargs: Any) -> None:
        self.server_message = server_message
        message = (
            "Adding songs to your custom catalog requires enterprise access that isn't "
            "enabled on your account.\n\n"
            "Note: the custom-catalog endpoint is for adding songs to your private "
            "fingerprint database, not for music recognition. If you intended to "
            "identify music, use recognize(...) (or recognize_enterprise(...) for "
            "files longer than 25 seconds) instead.\n\n"
            "To request custom-catalog access, contact api@audd.io.\n\n"
            f"[Server message: {server_message}]"
        )
        super().__init__(error_code, message, **kwargs)


class AudDInvalidRequestError(AudDAPIError):
    """50 / 51 / 600 / 601 / 602 / 700 / 701 / 702 / 906 — bad input from caller."""


class AudDInvalidAudioError(AudDAPIError):
    """300 / 400 / 500 — caller's audio file is the problem."""


class AudDRateLimitError(AudDAPIError):
    """611 — per-stream daily rate limit (and HTTP 429)."""


class AudDStreamLimitError(AudDAPIError):
    """610 — subscription stream slots exhausted."""


class AudDNotReleasedError(AudDAPIError):
    """907 — song hasn't been released yet."""


class AudDBlockedError(AudDAPIError):
    """19 family + 31337 — security/abuse/sanctions/IP ban/maintenance."""


class AudDNeedsUpdateError(AudDAPIError):
    """20 — app needs update / paid version required."""


class AudDServerError(AudDAPIError):
    """100 / 1000 / unknown codes / generic upstream failures."""


class AudDConnectionError(AudDError):
    """Network / TLS / timeout — no response received."""

    def __init__(self, message: str, *, original: BaseException | None = None) -> None:
        self.original = original
        super().__init__(message)


class AudDSerializationError(AudDError):
    """Server returned malformed JSON."""

    def __init__(self, message: str, *, raw_text: str = "") -> None:
        self.raw_text = raw_text
        super().__init__(message)


_CODE_MAP: dict[int, type[AudDAPIError]] = {
    900: AudDAuthenticationError,
    901: AudDAuthenticationError,
    903: AudDAuthenticationError,
    902: AudDQuotaError,
    904: AudDSubscriptionError,
    905: AudDSubscriptionError,
    50: AudDInvalidRequestError,
    51: AudDInvalidRequestError,
    600: AudDInvalidRequestError,
    601: AudDInvalidRequestError,
    602: AudDInvalidRequestError,
    700: AudDInvalidRequestError,
    701: AudDInvalidRequestError,
    702: AudDInvalidRequestError,
    906: AudDInvalidRequestError,
    300: AudDInvalidAudioError,
    400: AudDInvalidAudioError,
    500: AudDInvalidAudioError,
    610: AudDStreamLimitError,
    611: AudDRateLimitError,
    907: AudDNotReleasedError,
    19: AudDBlockedError,
    31337: AudDBlockedError,
    20: AudDNeedsUpdateError,
    100: AudDServerError,
    1000: AudDServerError,
}


def error_for_code(code: int) -> type[AudDAPIError]:
    """Map an AudD error code to its exception class. Unknown → AudDServerError."""
    return _CODE_MAP.get(code, AudDServerError)


def _branded_message(result: Any) -> str | None:
    """Extract branded artist/title text from an error response's `result`, if present."""
    if not isinstance(result, dict):
        return None
    artist = result.get("artist")
    title = result.get("title")
    if not (artist or title):
        return None
    parts = [p for p in (artist, title) if p]
    return " — ".join(str(p) for p in parts)


def raise_from_error_response(
    body: dict[str, Any],
    *,
    http_status: int,
    request_id: str | None,
    custom_catalog_context: bool = False,
) -> None:
    """Inspect a server `status: error` body and raise the appropriate exception."""
    err = body.get("error") or {}
    code = int(err.get("error_code", 0))
    message = str(err.get("error_message", ""))
    requested_params = body.get("request_params") or body.get("requested_params") or {}
    request_method = body.get("request_api_method")
    branded = _branded_message(body.get("result"))

    cls: type[AudDAPIError] = error_for_code(code)
    if custom_catalog_context and cls is AudDSubscriptionError:
        raise AudDCustomCatalogAccessError(
            code,
            server_message=message,
            http_status=http_status,
            request_id=request_id,
            requested_params=requested_params,
            request_method=request_method,
            branded_message=branded,
            raw_response=body,
        )
    raise cls(
        code,
        message,
        http_status=http_status,
        request_id=request_id,
        requested_params=requested_params,
        request_method=request_method,
        branded_message=branded,
        raw_response=body,
    )
