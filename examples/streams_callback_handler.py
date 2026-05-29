"""A minimal Flask handler that parses AudD callbacks.

Run: pip install flask
     AUDD_API_TOKEN=... python examples/streams_callback_handler.py

This file only prints the template — copy/paste into your own server. The
SDK doesn't ship a Flask dependency.
"""

TEMPLATE = '''\
import os
from flask import Flask, jsonify, request
from audd import AudD

app = Flask(__name__)
audd = AudD(api_token=os.environ["AUDD_API_TOKEN"])  # used for derive_longpoll_category, etc.


@app.post("/audd-callback")
def audd_callback():
    match, notif = audd.streams.handle_callback(request)
    if match is not None:
        print(f"radio={match.radio_id}  {match.song.artist} — {match.song.title}  "
              f"score={match.song.score}")
        for alt in match.alternatives:
            # Alternatives may have a different artist/title than the top match
            # (variant catalog releases, near-duplicates).
            print(f"  alt: {alt.artist} — {alt.title}  score={alt.score}")
    elif notif is not None:
        print(f"radio={notif.radio_id}  notification {notif.notification_code}: "
              f"{notif.notification_message}")
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
'''

if __name__ == "__main__":
    print(TEMPLATE)
