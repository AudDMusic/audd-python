"""Scan a folder of audio files, recognize each, tag and rename.

Walks a directory tree, runs `audd.recognize` on each audio file, writes the
artist/title/album/release_date back into the file's tags via mutagen, and
renames the file to "Artist - Title.ext".

Defaults to dry-run; pass --apply to actually mutate files. Reads the API
token from the AUDD_API_TOKEN environment variable.

mutagen is licensed LGPL-2.1-or-later — install separately:
    pip install mutagen
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from audd import AsyncAudD, AudDError
from audd.models import RecognitionResult

# mutagen is an optional runtime dependency for this example. Import lazily so
# `python main.py --help` still works without it installed.
try:
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3, TALB, TDRC, TIT2, TPE1, ID3NoHeaderError
    from mutagen.mp4 import MP4
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis
except ImportError as exc:  # pragma: no cover
    sys.exit(f"this example requires mutagen — `pip install mutagen` ({exc})")


AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".mp4", ".wav", ".aac",
})

# Characters that are illegal on Windows + macOS + Linux filesystems combined.
# We also strip control chars and trim trailing dots/spaces (Windows hates them).
_FORBIDDEN = '/\\:*?"<>|'
_MAX_NAME_LEN = 200


@dataclass
class Stats:
    scanned: int = 0
    matched: int = 0
    renamed: int = 0          # actually renamed (or "would rename" in dry-run)
    no_match: int = 0
    collisions: int = 0
    errors: int = 0
    error_details: list[str] = field(default_factory=list)


def sanitize_segment(s: str) -> str:
    """Make a string safe to use as part of a filename across OSes."""
    out = "".join(ch for ch in s if ord(ch) >= 32 and ch not in _FORBIDDEN)
    out = out.strip().rstrip(".")
    return out[:_MAX_NAME_LEN] or "_"


def target_name(artist: str, title: str, ext: str) -> str:
    return f"{sanitize_segment(artist)} - {sanitize_segment(title)}{ext.lower()}"


def _write_mp3_tags(path: Path, result: RecognitionResult) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    if result.artist:
        tags.add(TPE1(encoding=3, text=result.artist))
    if result.title:
        tags.add(TIT2(encoding=3, text=result.title))
    if result.album:
        tags.add(TALB(encoding=3, text=result.album))
    if result.release_date:
        tags.add(TDRC(encoding=3, text=result.release_date))
    tags.save(path)


def _write_vorbis_tags(file_obj: Any, result: RecognitionResult) -> None:
    """Shared logic for FLAC / OggVorbis / OggOpus — all use Vorbis comments."""
    if result.artist:
        file_obj["artist"] = result.artist
    if result.title:
        file_obj["title"] = result.title
    if result.album:
        file_obj["album"] = result.album
    if result.release_date:
        file_obj["date"] = result.release_date
    file_obj.save()


def _write_mp4_tags(path: Path, result: RecognitionResult) -> None:
    audio = MP4(path)
    if result.artist:
        audio["\xa9ART"] = [result.artist]
    if result.title:
        audio["\xa9nam"] = [result.title]
    if result.album:
        audio["\xa9alb"] = [result.album]
    if result.release_date:
        audio["\xa9day"] = [result.release_date]
    audio.save()


def write_tags(path: Path, result: RecognitionResult) -> None:
    """Dispatch tag-writing by extension. mutagen handles each format natively."""
    ext = path.suffix.lower()
    if ext == ".mp3":
        _write_mp3_tags(path, result)
    elif ext == ".flac":
        _write_vorbis_tags(FLAC(path), result)
    elif ext == ".ogg":
        _write_vorbis_tags(OggVorbis(path), result)
    elif ext == ".opus":
        _write_vorbis_tags(OggOpus(path), result)
    elif ext in (".m4a", ".mp4"):
        _write_mp4_tags(path, result)
    elif ext in (".wav", ".aac"):
        # WAV/AAC tag containers are inconsistent across files in the wild.
        # Skip tagging silently rather than corrupt anything; rename still happens.
        pass


def find_audio(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    )


async def process_one(
    audd: AsyncAudD,
    path: Path,
    *,
    index: int,
    total: int,
    apply: bool,
    stats: Stats,
    log: Callable[[str], None],
) -> None:
    prefix = f"[{index}/{total}] {path.name}"
    try:
        result = await audd.recognize(path)
    except AudDError as exc:
        stats.errors += 1
        stats.error_details.append(f"{path}: {exc}")
        log(f"{prefix} -> error: {exc}")
        return

    if result is None or not (result.artist and result.title):
        stats.no_match += 1
        log(f"{prefix} -> no match")
        return

    stats.matched += 1
    label = f"{result.artist} - {result.title}"
    new_name = target_name(result.artist, result.title, path.suffix)
    target = path.with_name(new_name)

    if target.exists() and target != path:
        stats.collisions += 1
        log(f"{prefix} -> matched {label!r}; target exists, skipping")
        return

    if not apply:
        stats.renamed += 1
        log(f"{prefix} -> matched {label!r} (would rename)")
        return

    try:
        write_tags(path, result)
    except Exception as exc:  # mutagen raises a zoo of types; surface them all
        stats.errors += 1
        stats.error_details.append(f"{path}: tag write failed: {exc}")
        log(f"{prefix} -> matched {label!r}; tag write failed: {exc}")
        return

    if target != path:
        try:
            path.rename(target)
        except OSError as exc:
            stats.errors += 1
            stats.error_details.append(f"{path}: rename failed: {exc}")
            log(f"{prefix} -> matched {label!r}; rename failed: {exc}")
            return

    stats.renamed += 1
    log(f"{prefix} -> matched {label!r}; tagged + renamed -> {new_name}")


async def run(folder: Path, *, apply: bool, concurrency: int) -> Stats:
    files = find_audio(folder)
    total = len(files)
    if total == 0:
        print(f"no audio files found under {folder}")
        return Stats()

    stats = Stats()
    sem = asyncio.Semaphore(concurrency)

    async with AsyncAudD() as audd:
        async def bounded(i: int, p: Path) -> None:
            async with sem:
                stats.scanned += 1
                await process_one(
                    audd, p, index=i, total=total,
                    apply=apply, stats=stats, log=print,
                )

        await asyncio.gather(*(bounded(i, p) for i, p in enumerate(files, start=1)))

    return stats


def print_summary(stats: Stats, *, apply: bool) -> None:
    verb = "renamed" if apply else "would rename"
    print()
    print("summary:")
    print(f"  scanned       {stats.scanned}")
    print(f"  matched       {stats.matched}")
    print(f"  {verb:13} {stats.renamed}")
    print(f"  no match      {stats.no_match}")
    print(f"  collisions    {stats.collisions}")
    print(f"  errors        {stats.errors}")
    if stats.error_details:
        print()
        print("errors:")
        for line in stats.error_details:
            print(f"  - {line}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recognize audio in a folder, tag, and rename to 'Artist - Title.ext'.",
    )
    parser.add_argument("folder", type=Path, help="folder to scan recursively")
    parser.add_argument(
        "--apply", action="store_true",
        help="actually write tags and rename files (default: dry-run)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=4,
        help="parallel recognition requests (default: 4)",
    )
    args = parser.parse_args()

    if not args.folder.is_dir():
        sys.exit(f"not a directory: {args.folder}")
    if args.concurrency < 1:
        sys.exit("--concurrency must be >= 1")

    if not args.apply:
        print("dry-run: pass --apply to actually tag and rename files")

    stats = asyncio.run(run(args.folder, apply=args.apply, concurrency=args.concurrency))
    print_summary(stats, apply=args.apply)


if __name__ == "__main__":
    main()
