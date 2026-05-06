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
