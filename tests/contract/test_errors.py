"""Error-shape fixtures."""
from __future__ import annotations

import pytest

from audd.errors import (
    AudDAPIError,
    AudDAuthenticationError,
    AudDBlockedError,
    AudDInvalidRequestError,
    AudDSubscriptionError,
    raise_from_error_response,
)


def test_900_invalid_token(load_fixture) -> None:
    payload = load_fixture("error_900_invalid_token.json")
    with pytest.raises(AudDAuthenticationError) as ei:
        raise_from_error_response(payload, http_status=200, request_id=None)
    assert ei.value.error_code == 900


def test_700_no_file(load_fixture) -> None:
    payload = load_fixture("error_700_no_file.json")
    with pytest.raises(AudDInvalidRequestError) as ei:
        raise_from_error_response(payload, http_status=200, request_id=None)
    assert ei.value.error_code == 700


def test_19_no_callback_url(load_fixture) -> None:
    payload = load_fixture("error_19_no_callback_url.json")
    with pytest.raises(AudDBlockedError) as ei:
        raise_from_error_response(payload, http_status=200, request_id=None)
    assert ei.value.error_code == 19


def test_902_stream_limit(load_fixture) -> None:
    payload = load_fixture("error_902_stream_limit.json")
    with pytest.raises(AudDAPIError) as ei:
        raise_from_error_response(payload, http_status=200, request_id=None)
    assert ei.value.error_code == 902


def test_904_enterprise_unauthorized(load_fixture) -> None:
    payload = load_fixture("error_904_enterprise_unauthorized.json")
    with pytest.raises(AudDSubscriptionError) as ei:
        raise_from_error_response(payload, http_status=200, request_id=None)
    assert ei.value.error_code == 904
