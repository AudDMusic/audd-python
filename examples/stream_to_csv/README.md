# stream_to_csv

Subscribe to a live audio stream via AudD and log every recognition to a CSV
file. Cleans up the stream on Ctrl-C.

## Run

```sh
pip install audd
export AUDD_API_TOKEN=...
python main.py "https://stream.example/live.m3u8"
python main.py "https://stream.example/live.m3u8" --output recordings.csv --radio-id 99999
```

CSV columns: `timestamp, radio_id, score, artist, title, album, song_link`.
Rows are flushed to disk after every match, so Ctrl-C won't lose data.

## Behavior

On startup, the script preserves your existing callback URL. If none is
configured, it temporarily points the account at `https://audd.tech/empty/`
(an AudD-operated 200 OK endpoint) so longpoll has somewhere to deliver to.
**It will not overwrite a callback URL you've already set in production.**

On exit (Ctrl-C / SIGTERM): the stream is deleted. If the script set the
fallback callback URL, that's left in place — there's no API method to clear
it, so you'd need to do that in the AudD dashboard if it bothers you.

Notification envelopes (e.g., stream stopped, audio format issue) print to
stderr instead of the CSV; only actual recognitions become rows.
