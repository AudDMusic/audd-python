"""Recognize a song from a URL.

Run: AUDD_API_TOKEN=your-token python examples/recognize_url.py
"""
import os

from audd import AudD


def main() -> None:
    audd = AudD(api_token=os.environ["AUDD_API_TOKEN"])
    result = audd.recognize("https://audd.tech/example.mp3")
    if result:
        print(f"{result.artist} — {result.title}")
    else:
        print("no match")


if __name__ == "__main__":
    main()
