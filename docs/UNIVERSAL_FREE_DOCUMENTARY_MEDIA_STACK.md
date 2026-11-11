# Universal Free Documentary Media Stack

This project uses a provider-agnostic, free-first pipeline for documentaries about history, science, space, technology, biographies, geography, disasters and current affairs.

## Core rule

Search for genuine media first. Verify the exact item, inspect the downloaded pixels, record item-level provenance and rights, and only then place it in the edit. Never describe a modern stock clip, generated image or reconstruction as historical footage. When genuine media cannot be found, use a clearly labelled diagram, map, timeline, chart or AI reconstruction—or leave the shot for editorial review. Do not insert unrelated filler to satisfy a shot count.

## Recommended tools

| Tool | Role | Cost / limits |
|---|---|---|
| FFmpeg + ffprobe | Video editing, transcodes, audio checks, subtitles, duration/resolution validation and frame extraction | Free, open source |
| Openverse API | Cross-provider discovery of openly licensed still images and audio | Free anonymous access with rate and pagination limits; not a general video-download API |
| Wikimedia Commons API | File-level image and video discovery with creator and licence metadata | Free; follow API etiquette and each file's licence |
| Internet Archive metadata API | Historical film discovery and item metadata | Free access; item rights vary and must be checked individually |
| NASA Images API / NASA SVS | Space, Earth science, spacecraft, missions and scientific visualizations | Free access; check NASA and third-party credit/usage notes per item |
| Library of Congress API | Historic photographs, films, maps and documents | Free access; item rights advisories vary |
| NARA Catalog API | U.S. federal archival moving images and records | API key may be required; access restrictions are not the same as copyright permission |
| DVIDS API | U.S. military/government imagery and video | API key; inspect item credits, terms and third-party material |
| Europeana API | European cultural heritage media | API key; only use items with supported item-level reuse rights |
| Smithsonian Open Access | Museum, science, history and object imagery / 3D assets | Free CC0 assets; API key registration may be required |
| OpenCV + Pillow | Frame sampling, image validation, dimensions and visual diagnostics | Free, open source |
| Whisper / faster-whisper | Local speech transcription and subtitle timing | Free, open source; compute and model-download costs are local/runner resources |
| Matplotlib | Evidence-based charts and scientific graphics | Free, open source |

Openverse is an image/audio discovery layer, not a source of guaranteed historical video. The current integration searches only commercially compatible CC0, Public Domain Mark, CC BY and CC BY-SA records. It stores the original item page, creator, licence and attribution. Non-commercial and no-derivatives licences are excluded from this automated commercial-use path.

## Current implementation in this repository

- Wikimedia Commons remains the first archival still-image search.
- Openverse is a fallback when Commons does not yield a suitable licensed still.
- Every downloaded Openverse image gets a .source.json sidecar.
- Openverse images are labelled openly_licensed_still_candidate; they are not automatically labelled authentic archival material.
- Gemini visual screening tries the configured GEMINI_MODEL and then GEMINI_FALLBACK_MODELS after HTTP 429 / 5xx responses. If all models fail, the result is unavailable, never a fabricated match.
- The review report still requires a human to verify the actual frame, historical context, rights, attribution and factual claims.

## Universal scene-to-media strategy

1. Convert each narration beat into a specific subject, action, location, date/period and intended visual type.
2. Generate several short, topic-specific search phrases (proper names, aliases, locations, dates, object names and event names).
3. Search video archives for genuine moving footage; search still-image archives independently rather than treating a video search miss as a total media miss.
4. Download only assets with an item page and explicit supported rights metadata. Record a sidecar at download time.
5. Inspect multiple frames, image dimensions, black frames, duration, aspect ratio, watermarks and duplicate reuse.
6. Compare the actual pixels to the narration. A metadata keyword score is only a discovery filter; it is not semantic proof.
7. If genuine footage is unavailable, use an accurate map, chart, timeline, technical diagram, public-domain document, or disclosed reconstruction. Never pass an illustration off as evidence.
8. Run audio silence/clipping checks, subtitle timing checks, 1080p/codec checks and a full human editorial review.
9. Keep the upload private/unlisted until the complete video and all source records are approved.

## GitHub-only zero-paid video mode

The `make-longform` GitHub Actions workflow hard-disables hosted AI-video providers:
`FREE_ONLY_MODE=1`, `AI_VIDEO_ENABLED=0`, and no Replicate credentials or model
configuration are passed to the render process. There is no workflow-dispatch switch
that can turn paid video generation back on. The workflow uses standard
`ubuntu-latest` CPU runners; it does not have a free GPU attached.

When a scene has no suitable moving footage, the pipeline uses the existing
scene-specific still-image fallback, then applies the project's FFmpeg cinematic
shot treatment and locally rendered graphics/charts/timelines. These are
animated edits of stills and generated graphics, **not AI-generated motion video**.
This keeps the GitHub workflow free of per-clip video-generation charges, but
does not promise that every shot will contain real moving footage.

Open-weight models such as Wan 2.2 are not a zero-compute shortcut: the official
Wan 2.2 repository documents substantial GPU requirements (for example, its
5B video model lists a 24 GB GPU for 720p inference). Standard GitHub-hosted
runners do not provide that GPU. A genuinely free, reliable GitHub-only GPU
video-generation service is therefore not assumed by this project.

## Important limits

- There is no universal free API that guarantees genuine, relevant, downloadable and commercially reusable video for every documentary topic.
- API access being free does not make every returned asset free to reuse.
- Public-domain claims, CC licences, government media policies and platform terms can have exceptions. Inspect the exact asset page and any third-party notices.
- AI visual matching cannot prove a historical event, person's identity, date, location, factual accuracy or copyright status.
- A quality score must not be forced to 100. A shot with missing rights or uncertain relevance should block publication, not be assigned a passing score.
- GitHub Actions has usage and storage limits; very large documentary artifacts may exceed practical free-tier limits.

## References

- Openverse API: https://api.openverse.org/v1/
- Openverse API documentation: https://docs.openverse.org/
- Wikimedia Commons API: https://www.mediawiki.org/wiki/API:Main_page
- NASA media usage guidance: https://www.nasa.gov/nasa-brand-center/images-and-media/
- Smithsonian Open Access: https://www.si.edu/openaccess
- Smithsonian developer tools: https://www.si.edu/openaccess/devtools
- FFmpeg: https://ffmpeg.org/
- OpenCV: https://opencv.org/
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
