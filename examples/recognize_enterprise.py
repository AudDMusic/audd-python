"""Recognize music in a long file via the enterprise endpoint.

Always pass `limit=` in dev to bound the chunk count and request cost.
Run: AUDD_API_TOKEN=... python examples/recognize_enterprise.py https://example.mp3
"""
import os
import sys

from audd import AudD


def main() -> None:
    token = os.environ.get("AUDD_API_TOKEN")
    if not token:
        sys.exit("AUDD_API_TOKEN required")
    if len(sys.argv) != 2:
        sys.exit("usage: recognize_enterprise.py <url>")
    audd = AudD(api_token=token)
    matches = audd.recognize_enterprise(sys.argv[1], limit=5, accurate_offsets=True)
    for m in matches:
        print(f"{m.timecode}  {m.artist} — {m.title}  (score {m.score})")


if __name__ == "__main__":
    main()
