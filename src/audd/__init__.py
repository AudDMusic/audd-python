"""Official Python SDK for the AudD music recognition API."""
from audd._version import __version__
from audd.client import AsyncAudD, AudD
from audd.errors import (
    AudDAPIError,
    AudDAuthenticationError,
    AudDBlockedError,
    AudDConnectionError,
    AudDCustomCatalogAccessError,
    AudDError,
    AudDInvalidAudioError,
    AudDInvalidRequestError,
    AudDNeedsUpdateError,
    AudDNotReleasedError,
    AudDQuotaError,
    AudDRateLimitError,
    AudDSerializationError,
    AudDServerError,
    AudDStreamLimitError,
    AudDSubscriptionError,
)
from audd.longpoll import AsyncLongpollConsumer, LongpollConsumer

__all__ = [
    "AsyncAudD",
    "AsyncLongpollConsumer",
    "AudD",
    "AudDAPIError",
    "AudDAuthenticationError",
    "AudDBlockedError",
    "AudDConnectionError",
    "AudDCustomCatalogAccessError",
    "AudDError",
    "AudDInvalidAudioError",
    "AudDInvalidRequestError",
    "AudDNeedsUpdateError",
    "AudDNotReleasedError",
    "AudDQuotaError",
    "AudDRateLimitError",
    "AudDSerializationError",
    "AudDServerError",
    "AudDStreamLimitError",
    "AudDSubscriptionError",
    "LongpollConsumer",
    "__version__",
]
