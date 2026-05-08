"""Unit tests for custom_catalog namespace."""
from __future__ import annotations

import httpx
import pytest
import respx

from audd import AudD
from audd.errors import AudDCustomCatalogAccessError


@respx.mock
def test_custom_catalog_add_success() -> None:
    respx.post("https://api.audd.io/upload/").mock(
        return_value=httpx.Response(200, json={"status": "success", "result": None}),
    )
    AudD(api_token="t").custom_catalog.add(audio_id=146, source="https://my.song.mp3")


@respx.mock
def test_custom_catalog_add_904_raises_specific_class() -> None:
    respx.post("https://api.audd.io/upload/").mock(
        return_value=httpx.Response(200, json={
            "status": "error",
            "error": {"error_code": 904, "error_message": "denied"},
        }),
    )
    with pytest.raises(AudDCustomCatalogAccessError) as ei:
        AudD(api_token="t").custom_catalog.add(audio_id=1, source="https://my.song.mp3")
    msg = str(ei.value)
    assert "is for adding songs" in msg
    assert "use recognize" in msg
    assert "api@audd.io" in msg
    assert "Server message: denied" in msg


def test_custom_catalog_add_docstring_warns_first() -> None:
    """The docstring must open with the NOT-FOR-RECOGNITION warning."""
    from audd.custom_catalog import CustomCatalog

    doc = CustomCatalog.add.__doc__ or ""
    assert "NOT how you submit audio for music recognition" in doc
    assert "recognize(" in doc
    assert "api@audd.io" in doc


@respx.mock
def test_custom_catalog_add_does_not_retry_on_transient_5xx() -> None:
    """Custom-catalog upload is metered: a transient 5xx must NOT trigger a
    retry, otherwise a successful-but-slow upload would double-charge.

    We seed three 5xx responses: a default-MUTATING policy with max_attempts=3
    would still only do one attempt (5xx isn't retried), so to make the
    no-retry behavior unambiguous we also assert call_count == 1.
    """
    route = respx.post("https://api.audd.io/upload/").mock(
        return_value=httpx.Response(500, json={"status": "error", "error": {
            "error_code": 300, "error_message": "internal",
        }}),
    )
    # _decode_success raises on the error body — exception type is incidental.
    with pytest.raises(Exception):
        AudD(api_token="t").custom_catalog.add(audio_id=1, source="https://my.song.mp3")
    assert route.call_count == 1


@respx.mock
def test_custom_catalog_add_does_not_retry_on_pre_upload_connect_error() -> None:
    """Even pre-upload connection errors (which MUTATING normally retries) are
    NOT retried for custom_catalog.add — metered upload, clean error preferred."""
    from audd.errors import AudDConnectionError

    call_count = {"n": 0}

    def _raise(_request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        raise httpx.ConnectError("dns failed")

    respx.post("https://api.audd.io/upload/").mock(side_effect=_raise)
    with pytest.raises(AudDConnectionError):
        AudD(api_token="t").custom_catalog.add(audio_id=1, source="https://my.song.mp3")
    assert call_count["n"] == 1
