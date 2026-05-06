"""A minimal Flask handler that parses AudD callbacks.

Run: pip install flask
     AUDD_API_TOKEN=... python examples/streams_callback_handler.py

This file only prints the template — copy/paste into your own server. The
SDK doesn't ship a Flask dependency.
"""

TEMPLATE = '''\
import os
from flask import Flask, request, jsonify
from audd import AudD

app = Flask(__name__)
audd = AudD(api_token=os.environ["AUDD_API_TOKEN"])  # used for derive_longpoll_category, etc.


@app.post("/audd-callback")
def audd_callback():
    payload = audd.streams.parse_callback(request.get_json(force=True))
    if payload.is_result:
        for r in payload.result.results:
            print(f"radio={payload.result.radio_id}  {r.artist} — {r.title}  score={r.score}")
    elif payload.is_notification:
        n = payload.notification
        print(f"radio={n.radio_id}  notification {n.notification_code}: {n.notification_message}")
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
'''

if __name__ == "__main__":
    print(TEMPLATE)
