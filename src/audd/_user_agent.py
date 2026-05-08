"""Build the User-Agent string sent on every request."""
from __future__ import annotations

import platform
import sys

from audd._version import __version__


def user_agent() -> str:
    """Return e.g. 'audd-python/0.1.0 python/3.12.1 (linux)'."""
    py = ".".join(map(str, sys.version_info[:3]))
    return f"audd-python/{__version__} python/{py} ({platform.system().lower()})"
