# audd-python

[![CI](https://github.com/AudDMusic/audd-python/actions/workflows/ci.yml/badge.svg)](https://github.com/AudDMusic/audd-python/actions/workflows/ci.yml)
[![Contract](https://github.com/AudDMusic/audd-python/actions/workflows/contract.yml/badge.svg)](https://github.com/AudDMusic/audd-python/actions/workflows/contract.yml)
[![PyPI](https://img.shields.io/pypi/v/audd.svg)](https://pypi.org/project/audd/)
[![Python versions](https://img.shields.io/pypi/pyversions/audd.svg)](https://pypi.org/project/audd/)

Official Python SDK for [music recognition API](https://audd.io): identify music from a short audio clip, a long audio file, or a live stream.

The API itself is so simple that it can easily be used even without an SDK: [docs.audd.io](https://docs.audd.io).

## Quickstart

```bash
pip install audd
```

Get your API token at [dashboard.audd.io](https://dashboard.audd.io).

Recognize from a URL:

```python
from audd import AudD

audd = AudD("your-api-token")
result = audd.recognize("https://audd.tech/example.mp3")
if result:
    print(f"{result.artist} — {result.title}")
```

Recognize from a local file:

```python
from audd import AudD

audd = AudD("your-api-token")
result = audd.recognize("/path/to/clip.mp3")
if result:
    print(f"{result.artist} — {result.title}")
```

`recognize()` accepts a URL, a filesystem path, a file-like object opened in binary mode, or `bytes` — it auto-detects. It returns a `RecognitionResult` on a match, or `None` when the clip isn't recognized.

For longer audio files (full-length songs, short-form videos, podcasts, broadcasts, DJ sets), use `recognize_enterprise(source, limit=...)` — it returns a `list[EnterpriseMatch]`, one per song detected across the file.

## Authentication

Pass the token positionally:

```python
audd = AudD("your-token")
```

Or omit it and set `AUDD_API_TOKEN` in the environment — the SDK reads it on construction:

```python
import os
os.environ["AUDD_API_TOKEN"] = "your-token"
audd = AudD()
```

For long-running services that rotate tokens (e.g., from a secret manager), call `audd.set_api_token(new_token)`. In-flight requests finish on the previous token; subsequent requests use the new one.

## What you get back

By default `recognize()` returns the core tags plus AudD's universal song link — no metadata-block opt-in needed:

```python
from audd import AudD

audd = AudD()
result = audd.recognize("https://audd.tech/example.mp3")
if result is None:
    raise SystemExit("no match")

# Core tags
print(result.artist, "—", result.title)
print(result.album, result.release_date, result.label)

# AudD's universal song page (works in any browser, links into all providers)
print(result.song_link)

# Helpers — driven off song_link, work without any return_metadata opt-in
print(result.thumbnail_url)             # cover-art URL, or None
print(result.streaming_url("spotify"))  # direct or lis.tn redirect, or None
print(result.streaming_urls())          # {"spotify": "...", "deezer": "...", ...}
```

If you need provider-specific metadata blocks, opt in per call. Request only what you need — each provider you ask for adds latency:

```python
result = audd.recognize(
    "https://audd.tech/example.mp3",
    return_metadata=["apple_music", "spotify"],
)
print(result.apple_music.url)        # direct Apple Music link
print(result.spotify.uri)            # spotify:track:...
print(result.spotify.preview_url)    # 30-second preview (only available via metadata block)
print(result.preview_url())          # first preview across requested providers, or None
```

Valid `return_metadata` values: `apple_music`, `spotify`, `deezer`, `napster`, `musicbrainz`. Attributes are `None` when not requested.

`EnterpriseMatch` (returned by `recognize_enterprise`) carries the same core tags plus `score`, `start_offset`, `end_offset`, `isrc`, `upc`. Access to `isrc`, `upc`, and `score` requires a Startup plan or higher — [contact us](mailto:api@audd.io) for enterprise features.

For ad-hoc inspection during development, `result.pretty_print()` dumps the full state — typed fields plus everything in `model_extra` — as indented JSON.

## Reading additional metadata

The typed models cover what AudD documents. To read additional fields the server returns, go through `model_extra`:

```python
result = audd.recognize("https://example.mp3", return_metadata=["apple_music"])

# Top-level extras
genre = result.model_extra.get("genre")

# Nested extras inside a typed metadata block
artwork = result.apple_music.model_extra.get("artwork")
```

This is the supported API for fields outside the typed surface. Beta features and per-account custom fields show up here.

For the **request** side, every call accepts an `extra_parameters` dict for sending additional form fields the typed kwargs don't cover:

```python
result = audd.recognize(
    "https://example.mp3",
    return_metadata="apple_music",
    extra_parameters={"some_beta_flag": "true"},
)
```

The same `extra_parameters` kwarg is on `recognize_enterprise`, `streams.set_callback_url`, and `streams.add`. Typed kwargs win on collision: if `extra_parameters={"return": "spotify"}` and `return_metadata="apple_music"` are both set, the request sends `return=apple_music`.

## Async

Same surface, with `await`:

```python
import asyncio
from audd import AsyncAudD

async def main():
    async with AsyncAudD() as audd:
        result = await audd.recognize("https://audd.tech/example.mp3")
        print(result)

asyncio.run(main())
```

`AsyncAudD` exposes the same `recognize`, `recognize_enterprise`, `streams`, `custom_catalog`, and `advanced` namespaces as `AudD`. Use `async with` (or `await audd.aclose()`) to release the underlying `httpx.AsyncClient`.

## Errors

Every server-side error becomes a typed exception. The hierarchy lets you handle whole families with one `except`:

```
AudDError
├── AudDConnectionError       # network / TLS / timeout
├── AudDSerializationError    # malformed JSON
└── AudDAPIError              # status=error from server
    ├── AudDAuthenticationError   # 900 / 901 / 903
    ├── AudDQuotaError            # 902
    ├── AudDSubscriptionError     # 904 / 905
    │   └── AudDCustomCatalogAccessError  # 904 from custom_catalog
    ├── AudDInvalidRequestError   # 50 / 51 / 600 / 601 / 602 / 700–702 / 906
    ├── AudDInvalidAudioError     # 300 / 400 / 500
    ├── AudDStreamLimitError      # 610
    ├── AudDRateLimitError        # 611
    ├── AudDNotReleasedError      # 907
    ├── AudDBlockedError          # 19 / 31337
    ├── AudDNeedsUpdateError      # 20
    └── AudDServerError           # 100 / 1000 / unknown
```

Idiomatic catch:

```python
from audd import AudD, AudDAuthenticationError, AudDInvalidAudioError, AudDAPIError

try:
    result = AudD().recognize("https://example.mp3")
except AudDAuthenticationError as e:
    raise SystemExit(f"check your token: [#{e.error_code}] {e.message}")
except AudDInvalidAudioError as e:
    print(f"audio rejected: {e.message}")
except AudDAPIError as e:
    # Catch-all for anything the server reported
    print(f"AudD #{e.error_code}: {e.message} (request_id={e.request_id})")
```

Every `AudDAPIError` carries `error_code`, `message`, `http_status`, `request_id`, `requested_params`, `request_method`, `branded_message`, and `raw_response` — enough to log a full incident or open a support ticket.

## Configuration

```python
import httpx
from audd import AudD

audd = AudD(
    "your-token",
    max_retries=3,            # per-call retry budget
    backoff_factor=0.5,       # initial backoff seconds (jittered)
    httpx_client=httpx.Client(proxy="http://corp-proxy:8080"),
    on_event=lambda e: print(e),
)
```

**Timeouts.** The default `httpx` timeouts are 30s connect / 60s read for standard endpoints, and 30s connect / 1 hour read for the enterprise endpoint (which can legitimately process multi-hour files). Override per call with `timeout=` (seconds).

**Retries.** Calls are classified by cost and retried accordingly:

| Class | Endpoints | Retried on |
|---|---|---|
| `RECOGNITION` | `recognize`, `recognize_enterprise`, `advanced.*` | network errors and 5xx **before** the upload reaches the server |
| `READ` | `streams.list`, `streams.get_callback_url`, longpoll | network errors and 5xx |
| `MUTATING` | `streams.set_callback_url`, `streams.add`, `streams.delete`, `custom_catalog.add` | network errors and 5xx (idempotent on the server) |

`RECOGNITION` will not double-bill your account: once the server has accepted bytes, a 5xx after that is surfaced rather than retried.

**Custom HTTP client.** Inject your own `httpx.Client` (sync) or `httpx.AsyncClient` (async) to add proxies, mTLS, custom transports, or shared connection pools. The SDK adds its `User-Agent` if you don't set one.

**Inspection.** Pass an `on_event=` callable to receive a frozen `AudDEvent` for every request / response / exception — useful for metrics, tracing, or dropping a `request_id` into your logs. Events never carry the api_token or request bytes; exceptions raised from the hook are swallowed so observability can't break the request path.

**Concurrency.** A single `AudD` (or `AsyncAudD`) instance is safe to share across threads, asyncio tasks, or worker processes — construct it once at startup and reuse it. The recommended pattern is one client per process.

## Streams

Real-time recognition off radio streams, broadcast feeds, and any other long-running URL. Configure once, then either receive callbacks on your server or poll for events.

```python
audd.streams.set_callback_url("https://your.server/audd-callback")
audd.streams.add("https://your.stream.url/listen.m3u8", radio_id=42)

for stream in audd.streams.list():
    print(stream.radio_id, stream.url, stream.stream_running)
```

Inside your callback receiver, hand the framework request to the SDK — it reads the body and parses it into a typed match or notification:

```python
# Flask, FastAPI, Django, aiohttp — all supported via duck-typing.
match, notif = audd.streams.handle_callback(request)
if match is not None:
    print(match.song.artist, "—", match.song.title, "score=", match.song.score)
    for alt in match.alternatives:
        # Alternatives may have a different artist/title than the top match
        # (variant catalog releases, near-duplicates).
        print("  alt:", alt.artist, "—", alt.title)
elif notif is not None:
    print("notification:", notif.notification_message)
```

`handle_callback(request)` reads + parses; on `AsyncAudD` it awaits the body read. If you already have the bytes (queue consumer, replay tool), call `audd.streams.parse_callback(body)` instead — it accepts a `dict`, `bytes`, or `str`.

#### Per-framework wiring

The same `audd.streams.handle_callback(request)` call works in any Python web framework — register a POST route and pass the request object in.

`Flask`:

```python
from flask import Flask, request

app = Flask(__name__)

@app.post("/audd/callback")
def audd_callback():
    match, notif = audd.streams.handle_callback(request)
    if match:
        print(match.song.artist, "—", match.song.title)
    return "", 200
```

`Django` (function-based view; add the URL to `urls.py`):

```python
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

@csrf_exempt
@require_POST
def audd_callback(request):
    match, notif = audd.streams.handle_callback(request)
    if match:
        print(match.song.artist, "—", match.song.title)
    return HttpResponse(status=200)
```

`aiohttp` (uses `AsyncAudD`):

```python
from aiohttp import web

async def audd_callback(request: web.Request) -> web.Response:
    match, notif = await audd.streams.handle_callback(request)
    if match:
        print(match.song.artist, "—", match.song.title)
    return web.Response(status=200)

app = web.Application()
app.router.add_post("/audd/callback", audd_callback)
```

For frameworks that hand you only the raw body bytes (Sanic, Starlette without `Request`, queue consumers, replay tools), call `audd.streams.parse_callback(body_bytes)` directly.

### Longpoll

If you can't expose a public callback URL, longpoll instead. AudD still requires a callback URL to be configured for the account (`https://audd.tech/empty/` works as a no-op receiver), and the SDK preflights this for you — pass `skip_callback_check=True` to skip if you've already verified.

The poll handle exposes three iterators — `matches`, `notifications`, `errors` — populated by a background thread (or task, in async). Use it as a context manager for clean shutdown:

```python
radio_id = 1  # any integer you choose — your handle for this stream

with audd.streams.longpoll(radio_id=radio_id, timeout=30) as poll:
    for match in poll.matches:
        print(match.song.artist, "—", match.song.title)
```

To consume matches, notifications, and errors concurrently, use `AsyncAudD` and `asyncio.gather`:

```python
import asyncio
from audd import AsyncAudD

async def main():
    async with AsyncAudD() as audd:
        category = audd.streams.derive_longpoll_category(42)
        poll = await audd.streams.longpoll(category, timeout=30)
        async with poll:
            async def consume_matches():
                async for m in poll.matches:
                    print(m.song.artist, "—", m.song.title)
            async def watch_errors():
                async for err in poll.errors:
                    print("terminal:", err)
                    return
            await asyncio.gather(consume_matches(), watch_errors())

asyncio.run(main())
```

`derive_longpoll_category` is a local computation: `MD5(MD5(api_token) + radio_id)[:9]`. The category alone is sufficient to subscribe — the api_token is never sent over the wire for longpolls.

#### Tokenless consumers

For browser widgets, embedded extensions, or any context where shipping the api_token would leak it: derive the category server-side, ship only the category to the consumer, and have the consumer use `LongpollConsumer`:

```python
from audd import LongpollConsumer

# `category` was derived on your server and shared with this process.
with LongpollConsumer(category="abc123def") as consumer:
    with consumer.iterate(timeout=30) as poll:
        for match in poll.matches:
            print(match.song.artist, "—", match.song.title)
```

`AsyncLongpollConsumer` is the async equivalent.

## Custom catalog (advanced)

> **The custom-catalog endpoint is NOT how you submit audio for music recognition.**
> For recognition, use `recognize()` (or `recognize_enterprise()` for longer audio files). The custom-catalog endpoint adds songs to your *private* fingerprint database so future `recognize()` calls on your account can identify *your own* tracks.
> Requires special access — contact api@audd.io.

```python
audd.custom_catalog.add(audio_id=42, source="https://my.song.mp3")
```

## Spec contract

This SDK is built against the [`audd-openapi`](https://github.com/AudDMusic/audd-openapi) spec. Contract tests in `tests/contract/` validate the parser against the canonical fixture set on every push, on a daily cron, and whenever the spec updates.

## License

MIT — see [LICENSE](./LICENSE).

## Support

- Documentation: <https://docs.audd.io>
- Tokens: <https://dashboard.audd.io>
- Issues: <https://github.com/AudDMusic/audd-python/issues>
- Email: api@audd.io
