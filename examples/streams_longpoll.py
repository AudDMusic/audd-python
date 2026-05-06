"""Listen for AudD recognition events via longpoll, no callback URL needed.

Note: the SDK preflights getCallbackUrl on the first iterate() call. If your
account doesn't have a callback URL configured, set one via:

    audd.streams.set_callback_url("https://audd.tech/empty/")

(or pass skip_callback_check=True to bypass the preflight, if you know what
you're doing).

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
    for payload in audd.streams.longpoll(category, timeout=30):
        print(payload)


if __name__ == "__main__":
    main()
