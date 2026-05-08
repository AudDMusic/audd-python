"""Recognize a song from a URL.

Run: python examples/recognize_url.py
"""
from audd import AudD


def main() -> None:
    audd = AudD(api_token="test")
    result = audd.recognize("https://audd.tech/example.mp3")
    if result:
        print(f"{result.artist} — {result.title}")
    else:
        print("no match")


if __name__ == "__main__":
    main()
