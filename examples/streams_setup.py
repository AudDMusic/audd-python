"""End-to-end stream setup: set callback URL, add a stream, verify.

Run: AUDD_API_TOKEN=... python examples/streams_setup.py
"""
import os

from audd import AudD


def main() -> None:
    audd = AudD(api_token=os.environ["AUDD_API_TOKEN"])
    audd.streams.set_callback_url("https://your-server.example.com/audd-callback")
    audd.streams.add(url="https://npr-ice.streamguys1.com/live.mp3", radio_id=1)
    for s in audd.streams.list():
        print(f"radio {s.radio_id}  running={s.stream_running}  category={s.longpoll_category}")


if __name__ == "__main__":
    main()
