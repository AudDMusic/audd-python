"""Auto-skip integration tests when AUDD_API_TOKEN is unset."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

_INTEGRATION_DIR = Path(__file__).parent


def pytest_collection_modifyitems(config, items):
    if "AUDD_API_TOKEN" in os.environ:
        return
    skip = pytest.mark.skip(
        reason="AUDD_API_TOKEN not set; skipping live-API integration tests",
    )
    # This hook sees every collected item, not only the ones in this
    # directory, so restrict the skip to the live-API tests.
    for item in items:
        if _INTEGRATION_DIR in Path(item.path).parents:
            item.add_marker(skip)
