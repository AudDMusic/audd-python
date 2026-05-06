"""Add a song to your private fingerprint catalog.

NOTE: this is NOT music recognition — for that, use recognize() instead.
This adds a song to YOUR private catalog so AudD can later identify it for
YOUR account only.

Run: AUDD_API_TOKEN=... python examples/custom_catalog_add.py https://my.song.mp3 42
"""
import os
import sys

from audd import AudD


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("usage: custom_catalog_add.py <song_url> <audio_id>")
    audd = AudD(api_token=os.environ["AUDD_API_TOKEN"])
    audd.custom_catalog.add(audio_id=int(sys.argv[2]), source=sys.argv[1])
    print("ok")


if __name__ == "__main__":
    main()
