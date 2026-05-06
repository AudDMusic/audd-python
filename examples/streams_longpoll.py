"""Listen for AudD recognition events via longpoll, no callback URL needed.

Note: the SDK preflights getCallbackUrl on longpoll(). If your account doesn't
have a callback URL configured, set one via:

    audd.streams.set_callback_url("https://audd.tech/empty/")

(or pass skip_callback_check=True to bypass the preflight, if you know what
you're doing).

The poll handle exposes three iterators — matches, notifications, errors —
populated by a background thread. Use as a context manager for clean shutdown.

Run: AUDD_API_TOKEN=... python examples/streams_longpoll.py 7
"""
import os
import sys

from audd import AudD


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: streams_longpoll.py <radio_id>")
    audd = AudD(api_token=os.environ["AUDD_API_TOKEN"])
    radio_id = int(sys.argv[1])
    category = audd.streams.derive_longpoll_category(radio_id)

    with audd.streams.longpoll(category, timeout=30) as poll:
        # Drain matches; notifications and errors are also available on the
        # corresponding iterators. For concurrent consumption of all three,
        # use AsyncAudD.streams.longpoll(...) with asyncio.gather.
        for match in poll.matches:
            print(f"{match.song.artist} — {match.song.title}  "
                  f"score={match.song.score}  radio={match.radio_id}")


if __name__ == "__main__":
    main()
