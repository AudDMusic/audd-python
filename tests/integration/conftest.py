"""Auto-skip integration tests when AUDD_API_TOKEN is unset."""
from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    if "AUDD_API_TOKEN" not in os.environ:
        skip = pytest.mark.skip(
            reason="AUDD_API_TOKEN not set; skipping live-API integration tests",
        )
        for item in items:
            item.add_marker(skip)
