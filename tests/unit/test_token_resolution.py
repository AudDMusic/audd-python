"""Tests for api_token resolution: explicit arg → AUDD_API_TOKEN env var → error."""
from __future__ import annotations

import re

import pytest

from audd import AsyncAudD, AudD


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUDD_API_TOKEN", raising=False)


def test_env_var_supplies_token_when_arg_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDD_API_TOKEN", "from-env")
    client = AudD()
    assert client.api_token == "from-env"
    client.close()


def test_explicit_arg_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDD_API_TOKEN", "from-env")
    client = AudD(api_token="explicit")
    assert client.api_token == "explicit"
    client.close()


def test_missing_token_and_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    with pytest.raises(ValueError, match=re.escape("dashboard.audd.io")):
        AudD()


def test_empty_string_token_and_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    with pytest.raises(ValueError):
        AudD(api_token="")


def test_async_env_var_pickup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDD_API_TOKEN", "from-env")
    client = AsyncAudD()
    assert client.api_token == "from-env"
