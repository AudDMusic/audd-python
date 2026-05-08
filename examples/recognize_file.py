"""Recognize a song from a local file.

Run: python examples/recognize_file.py path/to/song.mp3
"""
import sys

from audd import AudD


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: recognize_file.py <path>")
        sys.exit(1)
    audd = AudD(api_token="test")
    result = audd.recognize(sys.argv[1])
    print(result)


if __name__ == "__main__":
    main()
