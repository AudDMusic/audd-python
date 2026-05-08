# scan_and_rename

Walk a folder of audio files, recognize each with AudD, write the
`artist`/`title`/`album`/`date` back into the file's tags via
[mutagen](https://mutagen.readthedocs.io/), and rename to
`Artist - Title.ext`.

Defaults to **dry-run**. Pass `--apply` to actually mutate files.

## Run

```sh
pip install audd mutagen
export AUDD_API_TOKEN=...
python main.py /path/to/folder                       # dry-run
python main.py /path/to/folder --apply               # tag + rename
python main.py /path/to/folder --concurrency 8 --apply
```

Recognized extensions: `.mp3 .flac .ogg .opus .m4a .mp4 .wav .aac`. Tags are
written natively for each format; `.wav`/`.aac` are renamed but not tagged
(in-the-wild tag containers for those are inconsistent — better to skip than
corrupt). If the destination filename already exists, the file is left alone.

## License notes

- This example imports [mutagen](https://github.com/quodlibet/mutagen),
  which is licensed under **LGPL-2.1-or-later**. The `audd` SDK itself does
  not depend on mutagen — only this example does.

## `--apply` warning

`--apply` writes ID3/Vorbis/MP4 tags **in place** and renames the source
files. Run on a copy first; AudD's recognizer is excellent but not infallible
(homonyms, remixes, live versions, mashups). Dry-run output tells you exactly
what would change.
