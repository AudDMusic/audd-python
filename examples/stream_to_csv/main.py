"""Subscribe to a live audio stream, write each recognition to CSV.

Adds a stream to your AudD account, drives longpoll from this process, and
appends one CSV row per recognized song. Cleans up the stream and (best
effort) restores the previous callback URL on Ctrl-C.

Reads the API token from AUDD_API_TOKEN.
"""
from __future__ import annotations

import argparse
import csv
import os
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import FrameType
from typing import Any

from audd import AudD, AudDAPIError, StreamCallbackMatch, StreamCallbackNotification

# audd.tech/empty/ is a stable AudD-operated URL that returns 200 OK; using it
# as the callback URL lets longpoll deliver events even when you have no real
# webhook receiver. Documented behavior: docs.audd.io/streams.
EMPTY_CALLBACK_URL = "https://audd.tech/empty/"

# Server error code 19 = "no callback URL configured for this account".
_NO_CALLBACK_ERROR_CODE = 19

CSV_HEADER = ["timestamp", "radio_id", "score", "artist", "title", "album", "song_link"]


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def open_csv_for_append(path: Path) -> tuple[Any, Any]:
    """Open `path` for append; write the header if the file is new/empty."""
    fresh = not path.exists() or path.stat().st_size == 0
    fh = path.open("a", newline="", encoding="utf-8")
    writer = csv.writer(fh)
    if fresh:
        writer.writerow(CSV_HEADER)
        fh.flush()
    return fh, writer


def get_existing_callback_url(audd: AudD) -> str | None:
    """Return the configured callback URL, or None if none is set."""
    try:
        return audd.streams.get_callback_url()
    except AudDAPIError as exc:
        if exc.error_code == _NO_CALLBACK_ERROR_CODE:
            return None
        raise


def write_match_rows(match: StreamCallbackMatch, writer: Any, fh: Any) -> int:
    """Append one CSV row per song in the match (top match + alternatives)."""
    songs = [s for s in (match.song, *match.alternatives) if s is not None]
    for song in songs:
        writer.writerow([
            utc_now_iso(),
            match.radio_id if match.radio_id is not None else "",
            song.score if song.score is not None else "",
            song.artist or "",
            song.title or "",
            song.album or "",
            song.song_link or "",
        ])
    fh.flush()
    return len(songs)


def print_notifications(notifications: Any) -> None:
    n: StreamCallbackNotification
    for n in notifications:
        print(
            f"notification radio={n.radio_id} "
            f"code={n.notification_code}: {n.notification_message}",
            file=sys.stderr,
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Subscribe to an AudD stream and log recognitions to CSV.",
    )
    p.add_argument("url", help="audio stream URL (e.g. https://example/live.m3u8)")
    p.add_argument(
        "--output", type=Path, default=Path("recordings.csv"),
        help="CSV path to append rows to (default: recordings.csv)",
    )
    p.add_argument(
        "--radio-id", type=int, default=99999,
        help="radio_id to register the stream under (default: 99999)",
    )
    return p.parse_args()


def install_sigint_handler() -> None:
    """Translate SIGINT/SIGTERM into KeyboardInterrupt so the finally: block runs."""
    def _raise(signum: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, _raise)
    signal.signal(signal.SIGTERM, _raise)


def main() -> None:
    if "AUDD_API_TOKEN" not in os.environ:
        sys.exit("AUDD_API_TOKEN is not set")
    args = parse_args()
    install_sigint_handler()

    audd = AudD()
    previous_callback = get_existing_callback_url(audd)
    we_set_callback = False

    if previous_callback is None:
        audd.streams.set_callback_url(EMPTY_CALLBACK_URL)
        we_set_callback = True
        print(
            f"no callback URL was set; using {EMPTY_CALLBACK_URL} for the run",
            file=sys.stderr,
        )
    else:
        print(f"keeping existing callback URL: {previous_callback}", file=sys.stderr)

    audd.streams.add(args.url, radio_id=args.radio_id)
    category = audd.streams.derive_longpoll_category(args.radio_id)
    print(
        f"subscribed: radio_id={args.radio_id} category={category} -> {args.output}",
        file=sys.stderr,
    )

    fh, writer = open_csv_for_append(args.output)
    rows_written = 0

    try:
        with audd.streams.longpoll(category, timeout=50) as poll:
            threading.Thread(
                target=print_notifications, args=(poll.notifications,), daemon=True,
            ).start()
            for match in poll.matches:
                rows_written += write_match_rows(match, writer, fh)
            # matches ends only when the subscription stops; an error that
            # ended it is waiting here.
            for err in poll.errors:
                raise err
    except KeyboardInterrupt:
        print("\nstopping...", file=sys.stderr)
    finally:
        fh.close()
        try:
            audd.streams.delete(args.radio_id)
            print(f"stream {args.radio_id} deleted", file=sys.stderr)
        except Exception as exc:
            print(f"failed to delete stream {args.radio_id}: {exc}", file=sys.stderr)

        if we_set_callback:
            # The SDK doesn't expose a way to clear the callback URL, so the
            # account is now configured with audd.tech/empty/. Tell the user
            # explicitly — they can clear it from the dashboard if they want.
            print(
                f"note: the callback URL is still set to {EMPTY_CALLBACK_URL} "
                "(no API method to unset it; clear it via the dashboard if needed)",
                file=sys.stderr,
            )
        elif previous_callback is not None:
            # We didn't change anything; nothing to restore.
            pass

        audd.close()
        print(f"wrote {rows_written} row(s) to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
