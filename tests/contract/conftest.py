"""Shared fixture loader. Locates audd-openapi/fixtures/ via env or sibling dir."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

DEFAULT_PATH = Path(__file__).parent.parent.parent.parent / "audd-openapi" / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    env = os.environ.get("AUDD_OPENAPI_FIXTURES")
    p = Path(env) if env else DEFAULT_PATH
    if not p.is_dir():
        pytest.skip(f"audd-openapi fixtures not found at {p} (set AUDD_OPENAPI_FIXTURES env)")
    return p


@pytest.fixture
def load_fixture(fixtures_dir: Path):
    def _load(name: str) -> dict:
        return json.loads((fixtures_dir / name).read_text())
    return _load
