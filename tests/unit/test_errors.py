"""Unit tests for the exception hierarchy."""
from __future__ import annotations

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
    error_for_code,
    raise_from_error_response,
)


def test_hierarchy_inheritance() -> None:
    assert issubclass(AudDCustomCatalogAccessError, AudDSubscriptionError)
    assert issubclass(AudDSubscriptionError, AudDAPIError)
    assert issubclass(AudDAPIError, AudDError)
    assert issubclass(AudDConnectionError, AudDError)
    assert issubclass(AudDSerializationError, AudDError)


def test_code_mapping_900_authentication() -> None:
    assert error_for_code(900) is AudDAuthenticationError
    assert error_for_code(901) is AudDAuthenticationError
    assert error_for_code(903) is AudDAuthenticationError


def test_code_mapping_902_quota() -> None:
    assert error_for_code(902) is AudDQuotaError


def test_code_mapping_904_905_subscription() -> None:
    assert error_for_code(904) is AudDSubscriptionError
    assert error_for_code(905) is AudDSubscriptionError


def test_code_mapping_invalid_request() -> None:
    for c in (50, 51, 600, 601, 602, 700, 701, 702, 906):
        assert error_for_code(c) is AudDInvalidRequestError, c


def test_code_mapping_invalid_audio() -> None:
    for c in (300, 400, 500):
        assert error_for_code(c) is AudDInvalidAudioError, c


def test_code_mapping_rate_limit_611_only() -> None:
    assert error_for_code(611) is AudDRateLimitError


def test_code_mapping_stream_limit_610() -> None:
    assert error_for_code(610) is AudDStreamLimitError


def test_code_mapping_blocked_19_31337() -> None:
    assert error_for_code(19) is AudDBlockedError
    assert error_for_code(31337) is AudDBlockedError


def test_code_mapping_not_released_907() -> None:
    assert error_for_code(907) is AudDNotReleasedError


def test_code_mapping_needs_update_20() -> None:
    assert error_for_code(20) is AudDNeedsUpdateError


def test_code_mapping_server_error_fallbacks() -> None:
    assert error_for_code(100) is AudDServerError
    assert error_for_code(1000) is AudDServerError
    assert error_for_code(40) is AudDServerError


def test_code_mapping_unknown_code_is_server_error() -> None:
    assert error_for_code(99999) is AudDServerError


def test_raise_from_error_response_populates_fields() -> None:
    body = {
        "status": "error",
        "error": {"error_code": 900, "error_message": "Recognition failed: token bad"},
        "request_params": {"api_token": "d***e", "url": "https://x.mp3"},
        "request_api_method": "Recognition",
        "request_http_method": "POST",
    }
    try:
        raise_from_error_response(body, http_status=200, request_id="req-1")
    except AudDAuthenticationError as e:
        assert e.error_code == 900
        assert "token bad" in e.message
        assert e.http_status == 200
        assert e.request_id == "req-1"
        assert e.requested_params == {"api_token": "d***e", "url": "https://x.mp3"}
        assert e.request_method == "Recognition"
        assert e.branded_message is None
        assert e.raw_response == body


def test_raise_from_error_response_handles_enterprise_requested_params_field() -> None:
    body = {
        "status": "error",
        "error": {"error_code": 904, "error_message": "not authorized"},
        "requested_params": {"api_token": "d***e"},
    }
    try:
        raise_from_error_response(body, http_status=200, request_id=None)
    except AudDSubscriptionError as e:
        assert e.requested_params == {"api_token": "d***e"}


def test_raise_from_error_response_branded_message_for_19() -> None:
    body = {
        "status": "error",
        "error": {"error_code": 19, "error_message": "Recognition failed: blocked"},
        "result": {"artist": "Music recognition is powered by AudD.io", "title": "Blocked"},
    }
    try:
        raise_from_error_response(body, http_status=200, request_id=None)
    except AudDBlockedError as e:
        assert e.branded_message == "Music recognition is powered by AudD.io — Blocked"


def test_raise_from_error_response_custom_catalog_special_class() -> None:
    body = {"status": "error", "error": {"error_code": 904, "error_message": "denied"}}
    try:
        raise_from_error_response(body, http_status=200, request_id=None,
                                  custom_catalog_context=True)
    except AudDCustomCatalogAccessError as e:
        assert e.error_code == 904
        assert "custom-catalog endpoint is for adding songs" in str(e)


def test_str_contains_code_and_message() -> None:
    body = {"status": "error", "error": {"error_code": 700, "error_message": "no file"}}
    try:
        raise_from_error_response(body, http_status=200, request_id=None)
    except AudDInvalidRequestError as e:
        s = str(e)
        assert "700" in s
        assert "no file" in s
