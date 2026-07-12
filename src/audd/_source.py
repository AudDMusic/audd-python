"""Auto-detect what kind of audio source the caller passed and convert to the
right multipart fields.

We return a *re-opener* — a 0-arg callable that yields fresh form-data on
each call. The client invokes it inside the retry-wrapped request closure.

httpx specifically auto-seeks file handles between `post()` calls, so a
naive once-open implementation would not actually break in our case. The
re-opener is defensive: (a) it doesn't depend on any specific HTTP-library
behavior, (b) it raises cleanly on unseekable streams when a retry is
attempted (rather than silently sending an empty body), and (c) sibling
SDKs in other languages must follow this pattern because their HTTP
libraries may not auto-seek. The shape stays consistent across the family.
"""
from __future__ import annotations

from collections.abc import Callable
from io import IOBase
from pathlib import Path
from typing import Any

# Source: URL string, filesystem path (str or Path), file-like, or raw bytes.
Source = str | Path | IOBase | bytes | bytearray

# Output: (data, files_or_None, cleanup_or_None). `files` may be None for
# URL-source. `cleanup` closes any file handle the SDK itself opened for this
# attempt; it is None when there is nothing to close (URLs, bytes) or when the
# handle belongs to the caller (file-like sources are never closed by the SDK).
PreparedRequest = tuple[dict[str, Any], dict[str, Any] | None, Callable[[], None] | None]


def _looks_like_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def prepare_source(source: Any) -> Callable[[], PreparedRequest]:
    """Return a re-opener: a 0-arg callable that yields fresh (data, files, cleanup)
    on each call.

    URLs go in `data["url"]`; paths/file-likes/bytes go in `files["file"]`.

    For file paths and bytes, each call returns a fresh handle/buffer so that
    retried requests don't read from an exhausted source. For file-like
    objects (IOBase), each call seeks back to the original position before
    returning, if the object is seekable; if not, retrying that source
    raises immediately on attempt 2 (we'd send zero bytes otherwise).

    The third tuple element is a cleanup callable that closes the handle the
    SDK opened for the attempt (path sources); callers invoke it once the
    request attempt finishes. It is None when nothing needs closing —
    caller-owned file-like objects are deliberately never closed by the SDK.
    """
    # URL source: cheap, no body re-creation needed.
    if isinstance(source, str) and _looks_like_url(source):
        url = source
        def _do_url() -> PreparedRequest:
            return ({"url": url}, None, None)
        return _do_url

    # Filesystem path (str or Path): open a fresh handle each attempt.
    if isinstance(source, (str, Path)):
        path = Path(source)
        if isinstance(source, str) and not path.exists():
            # User probably mistyped a URL — give them a hint instead of FileNotFoundError.
            raise TypeError(
                f"{source!r} is not an HTTP URL (must start with http:// or https://) "
                f"and is not an existing file path. Pass a URL, a Path, a file-like, or bytes."
            )

        def _do_path() -> PreparedRequest:
            handle = path.open("rb")
            return ({}, {"file": (path.name, handle, "application/octet-stream")}, handle.close)
        return _do_path

    # Raw bytes: each attempt sends a copy.
    if isinstance(source, (bytes, bytearray)):
        buf = bytes(source)
        def _do_bytes() -> PreparedRequest:
            return ({}, {"file": ("upload.bin", buf, "application/octet-stream")}, None)
        return _do_bytes

    # File-like object: seek back to the original position on each attempt.
    if hasattr(source, "read"):
        fl: Any = source
        name = getattr(fl, "name", "upload.bin")
        try:
            start = fl.tell()
            seekable = True
        except (AttributeError, OSError):
            start = None
            seekable = False
        first_call = [True]

        def _do_filelike() -> PreparedRequest:
            if first_call[0]:
                first_call[0] = False
            else:
                if not seekable or start is None:
                    raise RuntimeError(
                        "Cannot retry an unseekable file-like source. Pass bytes "
                        "(buffer the content yourself) or use a Path / URL."
                    )
                fl.seek(start)
            # No cleanup: the handle belongs to the caller.
            return ({}, {"file": (name, fl, "application/octet-stream")}, None)
        return _do_filelike

    raise TypeError(
        f"Unsupported source type {type(source).__name__}; "
        "pass a URL string, a path (str or Path), a file-like object, or bytes."
    )
