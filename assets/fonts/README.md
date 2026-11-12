# Bundled caption fonts

| File | Family | Licence |
| --- | --- | --- |
| `NotoSans-Regular.ttf`, `NotoSans-Bold.ttf` | Noto Sans | SIL Open Font License 1.1 |
| `NotoSansDevanagari-Regular.ttf`, `NotoSansDevanagari-Bold.ttf` | Noto Sans Devanagari | SIL Open Font License 1.1 |

Source: <https://github.com/notofonts/notofonts.github.io>. Licence text: `OFL.txt`
(Copyright The Noto Project Authors).

These files make English, Hinglish and Hindi captions, slates and thumbnails
render identically on Windows, Linux and GitHub-hosted runners without relying
on whatever fonts happen to be installed. `core/fonts.py` resolves them and
`core/ffmpeg_utils.subtitles_filter` passes this directory to libass.

To use different fonts, set `SUBTITLE_FONTS_DIR` to a directory of `.ttf`/`.otf`
files and `SUBTITLE_FONT_LATIN` / `SUBTITLE_FONT_DEVANAGARI` to their family names.
