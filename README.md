# audd

[![CI](https://github.com/AudDMusic/audd-python/actions/workflows/ci.yml/badge.svg)](https://github.com/AudDMusic/audd-python/actions/workflows/ci.yml)
[![Contract](https://github.com/AudDMusic/audd-python/actions/workflows/contract.yml/badge.svg)](https://github.com/AudDMusic/audd-python/actions/workflows/contract.yml)
[![PyPI](https://img.shields.io/pypi/v/audd.svg)](https://pypi.org/project/audd/)
[![Python versions](https://img.shields.io/pypi/pyversions/audd.svg)](https://pypi.org/project/audd/)

Official Python SDK for the [AudD](https://audd.io) music recognition API.

## Quickstart

```bash
pip install audd
```

```python
from audd import AudD

audd = AudD(api_token="test")  # use your token from https://dashboard.audd.io
result = audd.recognize("https://audd.tech/example.mp3")
if result:
    print(f"{result.artist} — {result.title}")
```

## Capabilities

| What | How |
|---|---|
| Recognize a short clip (≤25s) | `audd.recognize(source)` |
| Recognize a long file (hours, days) | `audd.recognize_enterprise(source, limit=...)` |
| Manage stream recognition (set callback, longpoll for events) | `audd.streams.*` |

`source` accepts a URL, a file path, a file-like object, or raw bytes — auto-detected.

## Async

Use `AsyncAudD` instead — same surface:

```python
from audd import AsyncAudD

async def main():
    audd = AsyncAudD(api_token="test")
    try:
        result = await audd.recognize("https://audd.tech/example.mp3")
        print(result)
    finally:
        await audd.aclose()
```

## Errors

Every server error becomes a typed exception:

```python
from audd import AudD, AudDAuthenticationError, AudDSubscriptionError

try:
    AudD(api_token="bad").recognize("https://x.mp3")
except AudDAuthenticationError as e:
    print(f"check your token: {e.error_code} {e.message}")
except AudDSubscriptionError:
    print("this endpoint isn't enabled on your token")
```

The full hierarchy is documented in [`src/audd/errors.py`](src/audd/errors.py). Every `AudDAPIError` carries `error_code`, `message`, `http_status`, `request_id`, `requested_params`, `request_method`, `branded_message`, and `raw_response`.

## Forward compatibility

Models accept and round-trip unknown server fields via `model_extra`:

```python
result = audd.recognize("https://example.mp3", return_=["apple_music"])
print(result.apple_music.url)               # typed
print(result.model_extra)                   # any other unknown fields
```

If AudD adds a new metadata block tomorrow (e.g., `tidal`), you can read it as `result.model_extra["tidal"]` *today* — no SDK release needed. The next SDK release adds the typed `tidal` field, and both paths keep working.

## Streams

Manage real-time stream recognition (set callback, longpoll for events):

```python
audd.streams.set_callback_url("https://your.server/cb")
audd.streams.add("https://your.stream.url", radio_id=42)
for event in audd.streams.list():
    print(event)
```

### Receiving events without exposing your token

For browser widgets and other contexts where shipping the api_token would leak it,
derive a `category` server-side and share that with the consumer:

```python
from audd import LongpollConsumer

# `category` is derived server-side via AudD(...).streams.derive_longpoll_category(radio_id),
# then shared with the browser/widget. The consumer carries no api_token.
consumer = LongpollConsumer(category="abc123def")
for event in consumer.iterate(timeout=30):
    print(event)
```

## Configuration

```python
import httpx
from audd import AudD

audd = AudD(
    api_token="...",
    max_retries=3,           # retry budget per call
    backoff_factor=0.5,      # initial backoff seconds (jittered)
    httpx_client=httpx.Client(proxies="http://corp-proxy:8080"),
)
```

Default timeouts: 30s connect / 60s read for standard endpoints, 30s connect / **1 hour** read for the enterprise endpoint. Pass `timeout=` per call to override.

**Concurrency:** `AudD` and `AsyncAudD` are safe for concurrent use — share one instance across threads or asyncio tasks. `set_api_token(...)` rotates the token safely; in-flight requests continue with the old token, subsequent requests use the new one.

## Custom catalog (advanced — not for music recognition)

> ⚠ **The custom-catalog endpoint is NOT how you submit audio for music recognition.**
> For recognition, use `recognize()` or `recognize_enterprise()`. The custom-catalog
> endpoint adds songs to your private fingerprint database for *your* account.
> Requires special access — contact api@audd.io if you need it.

```python
audd.custom_catalog.add(audio_id=42, source="https://my.song.mp3")
```

## Advanced

For endpoints not yet wrapped by typed methods on this SDK, use the raw-request escape hatch:

```python
raw = audd.advanced.raw_request("someNewMethod", {"q": "x"})
```

## Spec contract

This SDK builds against the [`audd-openapi`](https://github.com/AudDMusic/audd-openapi) spec. The contract tests in `tests/contract/` validate the parser against the canonical fixture set on every push, on a daily cron, and on every spec update.

## License

MIT — see [LICENSE](./LICENSE).

## Support

- Documentation: https://docs.audd.io
- Tokens: https://dashboard.audd.io
- Email: api@audd.io
