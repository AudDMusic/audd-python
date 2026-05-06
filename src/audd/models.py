"""Typed models. Forward-compat: extra='allow' on every model so unknown fields
round-trip through `model_extra` and attribute access."""
from __future__ import annotations

import sys
from typing import Any, Literal, TextIO
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

# Streaming providers reachable via the lis.tn `?<provider>` redirect helper.
_STREAMING_PROVIDERS: tuple[str, ...] = (
    "spotify", "apple_music", "deezer", "napster", "youtube",
)
StreamingProvider = Literal["spotify", "apple_music", "deezer", "napster", "youtube"]


def _lis_tn_streaming_url(song_link: str | None, provider: str) -> str | None:
    """Return ``f"{song_link}?{provider}"`` only when ``song_link`` is a lis.tn URL.

    Returns None for non-lis.tn (e.g., YouTube song_links) and when no
    song_link is present. lis.tn supports redirect query params for each
    provider.
    """
    if not song_link:
        return None
    parsed = urlparse(song_link)
    if parsed.hostname != "lis.tn":
        return None
    sep = "&" if parsed.query else "?"
    return f"{song_link}{sep}{provider}"


class _Forward(BaseModel):
    """Base for every model — accepts unknown fields and exposes them in model_extra."""

    model_config = ConfigDict(extra="allow", populate_by_name=True, str_strip_whitespace=False)


class AppleMusicMetadata(_Forward):
    artistName: str | None = None
    url: str | None = None
    durationInMillis: int | None = None
    name: str | None = None
    isrc: str | None = None
    albumName: str | None = None
    trackNumber: int | None = None
    composerName: str | None = None
    discNumber: int | None = None
    releaseDate: str | None = None


class SpotifyMetadata(_Forward):
    id: str | None = None
    name: str | None = None
    duration_ms: int | None = None
    explicit: bool | None = None
    popularity: int | None = None
    track_number: int | None = None
    type: str | None = None
    uri: str | None = None


class DeezerMetadata(_Forward):
    id: int | None = None
    title: str | None = None
    duration: int | None = None
    link: str | None = None


class NapsterMetadata(_Forward):
    id: str | None = None
    name: str | None = None
    isrc: str | None = None
    artistName: str | None = None
    albumName: str | None = None


class MusicBrainzEntry(_Forward):
    id: str
    score: int | str | None = None
    title: str | None = None
    length: int | None = None


class RecognitionResult(_Forward):
    timecode: str
    audio_id: int | None = None
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    release_date: str | None = None
    label: str | None = None
    song_link: str | None = None
    isrc: str | None = None
    upc: str | None = None
    apple_music: AppleMusicMetadata | None = None
    spotify: SpotifyMetadata | None = None
    deezer: DeezerMetadata | None = None
    napster: NapsterMetadata | None = None
    musicbrainz: list[MusicBrainzEntry] | None = None

    def __repr__(self) -> str:
        parts: list[str] = []
        if self.artist:
            parts.append(f"artist={self.artist!r}")
        if self.title:
            parts.append(f"title={self.title!r}")
        parts.append(f"timecode={self.timecode!r}")
        if self.song_link:
            parts.append(f"song_link={self.song_link!r}")
        return f"<RecognitionResult {' '.join(parts)}>"

    def pretty_print(self, *, stream: TextIO | None = None) -> None:
        """Print the full model state — typed fields plus any extras — as indented JSON.

        Useful during development to see exactly what the API returned. The
        default :py:meth:`__repr__` is intentionally terse for logs; reach for
        this when you want everything.
        """
        out = stream if stream is not None else sys.stdout
        out.write(self.model_dump_json(indent=2))
        out.write("\n")

    @property
    def is_custom_match(self) -> bool:
        return self.audio_id is not None

    @property
    def is_public_match(self) -> bool:
        return self.audio_id is None and (self.artist is not None or self.title is not None)

    @property
    def thumbnail_url(self) -> str | None:
        """Cover-art URL for lis.tn-hosted song_links, else None.

        Appends `?thumb` (or `&thumb` if the link already has a query) — only for
        hosts where AudD's image endpoint exists. YouTube and other hosts return None.
        """
        return _lis_tn_streaming_url(self.song_link, "thumb")

    def streaming_url(self, provider: StreamingProvider) -> str | None:
        """Direct or redirect URL for a streaming provider, with smart fallback.

        Resolution order:

        1. **Direct URL from the metadata block** when the user requested that
           provider via ``return=`` (e.g. ``apple_music.url``,
           ``spotify.external_urls["spotify"]``, ``deezer.link``,
           ``napster.href``). Direct = no redirect, faster for clients.
        2. **lis.tn redirect** ``f"{song_link}?{provider}"`` when ``song_link``
           is a lis.tn URL. Works regardless of whether ``return=`` was set.
        3. ``None`` when neither path resolves (e.g., YouTube ``song_link`` and
           the user didn't request the provider's metadata).

        Valid providers: ``"spotify"``, ``"apple_music"``, ``"deezer"``,
        ``"napster"``, ``"youtube"``. (``"youtube"`` has no metadata-block
        fallback — only the lis.tn redirect path.)
        """
        if provider not in _STREAMING_PROVIDERS:
            raise ValueError(
                f"Unknown streaming provider: {provider!r}. "
                f"Valid: {', '.join(_STREAMING_PROVIDERS)}",
            )
        direct = self._direct_streaming_url(provider)
        if direct is not None:
            return direct
        return _lis_tn_streaming_url(self.song_link, provider)

    def _direct_streaming_url(self, provider: str) -> str | None:
        """Pull a direct URL out of the corresponding metadata block, if present."""
        if provider == "apple_music" and self.apple_music is not None:
            url = getattr(self.apple_music, "url", None)
            if isinstance(url, str) and url:
                return url
        elif provider == "spotify" and self.spotify is not None:
            extra = getattr(self.spotify, "model_extra", None) or {}
            ext_urls = extra.get("external_urls")
            if isinstance(ext_urls, dict):
                url = ext_urls.get("spotify")
                if isinstance(url, str) and url:
                    return url
            uri = getattr(self.spotify, "uri", None)
            if isinstance(uri, str) and uri:
                return uri
        elif provider == "deezer" and self.deezer is not None:
            link = getattr(self.deezer, "link", None)
            if isinstance(link, str) and link:
                return link
        elif provider == "napster" and self.napster is not None:
            extra = getattr(self.napster, "model_extra", None) or {}
            href = extra.get("href")
            if isinstance(href, str) and href:
                return href
        # youtube has no metadata block; lis.tn redirect is the only path.
        return None

    def streaming_urls(self) -> dict[str, str]:
        """All providers with a resolvable URL — direct or via lis.tn redirect.

        Returns ``{provider: url}`` for every provider where either the
        metadata block carries a direct URL OR the ``song_link`` is a lis.tn
        URL. Empty dict if neither path resolves for any provider.
        """
        out: dict[str, str] = {}
        for p in _STREAMING_PROVIDERS:
            url = self.streaming_url(p)  # type: ignore[arg-type]
            if url is not None:
                out[p] = url
        return out

    def preview_url(self) -> str | None:
        """First available 30-second audio preview URL, in priority order.

        Picks the first non-empty URL from ``apple_music.previews[0].url``,
        then ``spotify.preview_url``, then ``deezer.preview``. Returns None if
        no metadata block carries a preview.

        **Note:** previews are governed by their respective providers'
        terms of use (Apple Music, Spotify, Deezer). The SDK consumer is
        responsible for honoring those terms — including caching restrictions,
        attribution requirements, and any redistribution constraints.
        """
        # Apple Music: previews is a list of {"url": "..."} entries.
        if self.apple_music is not None:
            previews = getattr(self.apple_music, "previews", None)
            if previews is None:
                # Forward-compat: the field may live in model_extra if not typed.
                extra = getattr(self.apple_music, "model_extra", None) or {}
                previews = extra.get("previews")
            if isinstance(previews, list) and previews:
                first = previews[0]
                if isinstance(first, dict):
                    url = first.get("url")
                    if isinstance(url, str) and url:
                        return url
        # Spotify: preview_url field directly.
        if self.spotify is not None:
            spurl = getattr(self.spotify, "preview_url", None)
            if spurl is None:
                extra = getattr(self.spotify, "model_extra", None) or {}
                spurl = extra.get("preview_url")
            if isinstance(spurl, str) and spurl:
                return spurl
        # Deezer: preview field directly.
        if self.deezer is not None:
            dz = getattr(self.deezer, "preview", None)
            if dz is None:
                extra = getattr(self.deezer, "model_extra", None) or {}
                dz = extra.get("preview")
            if isinstance(dz, str) and dz:
                return dz
        return None


class EnterpriseMatch(_Forward):
    score: int
    timecode: str
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    release_date: str | None = None
    label: str | None = None
    isrc: str | None = None
    upc: str | None = None
    song_link: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None

    def __repr__(self) -> str:
        parts: list[str] = []
        if self.artist:
            parts.append(f"artist={self.artist!r}")
        if self.title:
            parts.append(f"title={self.title!r}")
        parts.append(f"timecode={self.timecode!r}")
        parts.append(f"score={self.score!r}")
        if self.song_link:
            parts.append(f"song_link={self.song_link!r}")
        return f"<EnterpriseMatch {' '.join(parts)}>"

    def pretty_print(self, *, stream: TextIO | None = None) -> None:
        """Print the full model state as indented JSON.

        See :py:meth:`RecognitionResult.pretty_print`.
        """
        out = stream if stream is not None else sys.stdout
        out.write(self.model_dump_json(indent=2))
        out.write("\n")

    @property
    def thumbnail_url(self) -> str | None:
        """Cover-art URL for lis.tn-hosted song_links, else None."""
        return _lis_tn_streaming_url(self.song_link, "thumb")

    def streaming_url(self, provider: StreamingProvider) -> str | None:
        """Redirect URL for a streaming provider — see RecognitionResult.streaming_url."""
        if provider not in _STREAMING_PROVIDERS:
            raise ValueError(
                f"Unknown streaming provider: {provider!r}. "
                f"Valid: {', '.join(_STREAMING_PROVIDERS)}",
            )
        return _lis_tn_streaming_url(self.song_link, provider)

    def streaming_urls(self) -> dict[str, str]:
        """All five providers' redirect URLs — see RecognitionResult.streaming_urls."""
        if not self.song_link or urlparse(self.song_link).hostname != "lis.tn":
            return {}
        out: dict[str, str] = {}
        for p in _STREAMING_PROVIDERS:
            url = _lis_tn_streaming_url(self.song_link, p)
            if url is not None:
                out[p] = url
        return out


class EnterpriseChunkResult(_Forward):
    songs: list[EnterpriseMatch]
    offset: str


class Stream(_Forward):
    radio_id: int
    url: str
    stream_running: bool
    longpoll_category: str | None = None


class StreamCallbackResultEntry(_Forward):
    artist: str
    title: str
    score: int
    album: str | None = None
    release_date: str | None = None
    label: str | None = None
    song_link: str | None = None
    apple_music: AppleMusicMetadata | None = None
    spotify: SpotifyMetadata | None = None
    deezer: DeezerMetadata | None = None
    napster: NapsterMetadata | None = None
    musicbrainz: list[MusicBrainzEntry] | None = None


class StreamCallbackResult(_Forward):
    radio_id: int
    timestamp: str | None = None
    play_length: int | None = None
    results: list[StreamCallbackResultEntry]


class StreamCallbackNotification(_Forward):
    radio_id: int
    stream_running: bool | None = None
    notification_code: int
    notification_message: str


class StreamCallbackPayload:
    """Wrapper over a callback payload — recognition result or notification."""

    __slots__ = ("notification", "raw_payload", "result", "time")

    def __init__(
        self,
        *,
        result: StreamCallbackResult | None,
        notification: StreamCallbackNotification | None,
        time: int | None,
        raw_payload: Any,
    ) -> None:
        self.result = result
        self.notification = notification
        self.time = time
        self.raw_payload = raw_payload

    @property
    def is_result(self) -> bool:
        return self.result is not None

    @property
    def is_notification(self) -> bool:
        return self.notification is not None

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> StreamCallbackPayload:
        if "notification" in payload:
            return cls(
                result=None,
                notification=StreamCallbackNotification.model_validate(payload["notification"]),
                time=payload.get("time"),
                raw_payload=payload,
            )
        inner = payload.get("result") or {}
        return cls(
            result=StreamCallbackResult.model_validate(inner),
            notification=None,
            time=None,
            raw_payload=payload,
        )


class LyricsResult(_Forward):
    artist: str
    title: str
    lyrics: str | None = None
    song_id: int | None = None
    media: str | None = None
    full_title: str | None = None
    artist_id: int | None = None
    song_link: str | None = None
