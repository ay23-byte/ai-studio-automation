# Architecture and operations

This document describes how the three pipelines work after the production audit,
what protects each stage, and how to operate and recover the system. Design
decisions that close a specific failure are marked **Root cause**.

## 1. Pipelines at a glance

| Pipeline | Entry point | Schedule | Output | Upload |
| --- | --- | --- | --- | --- |
| Shorts | `run.py shorts`, `run_daily.py --format shorts` | `make_shorts.yml`, 09:00 IST | `output/shorts_<id>.mp4` (1080x1920, 30 fps) | Private, automatic |
| Long-form documentary | `run.py long`, `run_daily.py --format long` | `make_longform.yml`, manual | `output/longform_<id>.mp4` (1920x1080, 30 fps) | Private, after release certification |
| Daily news Short | `scripts/run_daily_news_short.py` | `daily-news-auto-short.yml`, 08:15 and 19:15 IST | `output/shorts_<id>.mp4` | Private, automatic |

All three share the same stages:

```
topic ─► duplicate check ─► script ─► content QA ─► narration ─► visuals ─► captions
      ─► render ─► technical QA ─► release gate ─► private upload ─► read-back ─► ledger ─► notify
```

Every stage runs through `core/jobs.Checkpoint`, so a job that fails can be
re-run with `python run.py resume --id N` and continues from the failed stage.

## 2. Module map

| Concern | Module | Notes |
| --- | --- | --- |
| Configuration | `core/config.py` | `.env` loading, path overrides (`AI_STUDIO_*`), channel identity |
| Database | `core/db.py` | Additive migrations, backups, transactions, stage log, ledger mirror |
| Checkpoints | `core/jobs.py` | `build/job_<id>/checkpoint.json`, input hashes, output-file validation |
| Topics | `core/topics.py`, `config/topic_bank.json` | Locked atomic queue, model proposals, curated fallback bank |
| Duplicates | `core/dedupe.py` | Subject similarity, `state/publish_ledger.json`, upload markers |
| Script models | `core/script_engine.py`, `core/llm.py` | Gemini with model fallback, then free OpenAI-compatible providers |
| Content QA | `core/content_qa.py` | Structure, length, language/script, risky-claim patterns |
| Narration | `core/voice.py` | Requested engine → Edge voices → offline `espeak-ng` |
| Visuals | `core/images.py`, `core/footage_sources.py`, `core/explainer_cards.py` | Circuit breakers; disclosed card fallback |
| Fonts and captions | `core/fonts.py`, `core/subtitles_export.py`, `core/subtitle_translation.py` | Bundled Noto fonts; SRT/VTT sidecars |
| Rendering | `core/editor_shorts.py`, `core/editor_longform.py`, `core/render_join.py`, `core/ffmpeg_utils.py` | Batched joins, partial outputs, escaped filter paths |
| Technical QA | `core/quality.py` | Resolution, codec, pixel format, A/V sync, black/silence, repetition |
| Release gate | `core/release_gate.py`, `core/release_certification.py` | One gate for every upload path |
| Upload | `core/uploader.py`, `core/publish.py` | Channel lock, private-only, markers, read-back |
| Autonomous news | `core/news_auto.py`, `core/news_source_verification.py` | Source-tier gate, sensitivity filter, grounded scripts |
| Notifications | `core/notify.py` | Telegram, Discord, Slack, webhook, job summary, local log |
| Health | `core/health.py` | `python run.py doctor` |
| CI state | `scripts/persist_state.py` | Ledger on the `automation-state` branch |

## 3. Topic selection and duplicate prevention

**Root cause.** `topics.json` was exhausted, after which the scheduler cycled a
hard-coded five-item finance list. Nothing recorded what had been produced, so
the same subject was rebuilt and uploaded (three "Compound Interest" Shorts
exist in the database). On GitHub-hosted runners the checkout is fresh every
run, so even a local record would have been lost.

Selection order (`run_daily.py`):

1. First approved, pending entry in `topics.json` that is not a duplicate.
   Duplicates are marked `skipped` with the reason instead of being rebuilt.
2. Topics proposed by the script model, given the list of covered titles.
3. The curated evergreen bank in `config/topic_bank.json` (60 topics).

A candidate is a duplicate when it matches, in any layer:

| Layer | Source | Survives a fresh checkout |
| --- | --- | --- |
| Ledger | `state/publish_ledger.json` | Yes, via the `automation-state` branch |
| Database | finished or uploaded jobs in SQLite | Local machines only |
| Channel | an upload carrying `AI_STUDIO_DEDUPE:<kind>:<hash>` in its description | Yes |

Matching is by normalised subject key, by token similarity of titles
(`DEDUPE_SIMILARITY_THRESHOLD`, default 0.6, filler words in English, Hinglish
and Hindi removed) and, for news, by feed item id. The check runs before
scripting, again on the generated title, and again before upload.

Known limit: a subject phrased in different languages ("How do black holes
form" versus "Black hole kaise banta hai") shares too few tokens to match.
Queue topics in one language, or set the same `topic` field on both.

`topics.json` is written with a lock file and an atomic replace, so overlapping
runs cannot corrupt it. `--force` builds a duplicate deliberately.

### State on CI

`scripts/persist_state.py restore` runs before a scheduled build and `save`
after it. State lives on the orphan branch `automation-state` and contains JSON
only. Concurrent writers are merged entry by entry and an upload record is never
lost in a merge. Workflows no longer push to `main`.

## 4. Scripts and content QA

`core/script_engine._ask_gemini` tries the configured Gemini model, then the
models in `GEMINI_FALLBACK_MODELS`, then any OpenAI-compatible provider with a
key (`GROQ_API_KEY`, `CEREBRAS_API_KEY`, `OPENROUTER_API_KEY`,
`MISTRAL_API_KEY`, `GITHUB_MODELS_TOKEN`, local Ollama). Keys are sent in
headers and redacted from every error message.

**Root cause.** A Shorts script went straight from the model to narration.
Truncated JSON, a 300-word "Short", the wrong script for the language or a
promised return was only seen by a person after the video existed.

`core/content_qa.validate_shorts_script` returns PASS, REVIEW or FAIL. A FAIL
triggers one corrective regeneration that quotes the reasons
(`SHORTS_SCRIPT_ATTEMPTS`). The same check runs again in the release gate, so a
hand-edited script cannot bypass it.

Content QA is deterministic. It checks form and known risky patterns; it does
not establish that a statement is true. Factual accuracy of Shorts still depends
on the model and on the private review before publication.

## 5. Narration, timing and captions

Scene durations are derived from the narration timings by
`core/timing.align_units`, so each visual changes when its sentence starts.

**Root cause.** Captions depended on fonts installed on the machine. On a host
without a Devanagari font, Hindi rendered as empty boxes.

`assets/fonts/` bundles Noto Sans and Noto Sans Devanagari (SIL OFL 1.1). The
`subtitles` filter is always given `fontsdir`, and the ASS style selects the
Devanagari family when the text needs it. Filter paths are escaped by
`core/ffmpeg_utils.filter_path`, which is what makes Windows paths with drive
letters and spaces work.

Long-form caption language is chosen with `--subtitle-language` or
`SUBTITLE_LANGUAGE` (`english`, `hindi`, `hinglish`, `source`). Captions already
in the target language are not sent to a model. The translation stage runs
before visuals are fetched, so a missing key fails in seconds rather than after
the expensive work.

Every job also writes `captions.<lang>.srt` and `.vtt` for upload as closed
captions.

Limit: Pillow needs `libraqm` to join Devanagari letters correctly. Burned-in
captions use libass and are unaffected; Hindi text on thumbnails and cards is
skipped when `libraqm` is missing (`run.py doctor` reports this; the workflows
install `libraqm0`).

## 6. Visual sourcing and the fallback card

Order for a Shorts scene: local AI video (only if configured) → generated image
→ stock clip that passes the relevance gate → explainer card.

**Root cause.** One unreachable image provider cost a full retry and back-off
cycle for every scene, and when all providers were down the job failed after
narration had been produced.

- Circuit breakers in `core/images.py` and `core/footage_sources.py` stop
  calling a host for the rest of the run after repeated network failures.
- `core/explainer_cards.py` draws an original, text-safe graphic as the last
  resort. Each card has a provenance sidecar and is listed in
  `visual_fallback_report.json`.
- Cards are disclosed, never silent. When their share exceeds
  `VISUAL_FALLBACK_MAX_SHARE` (0.5 for Shorts, 0.35 for long-form) the quality
  report sets `upload_blocked` and the release gate refuses the upload until a
  person sets `ALLOW_FALLBACK_VISUAL_UPLOAD=1`.
- On resume, scenes that fell back to a card are retried against the providers.
- Archival beats never receive a synthetic substitute.

`asset_manifest.json`, `licenses.csv` and `credits.txt` record the source and
licence of every asset used in a Short.

## 7. Rendering

- Segments are rendered into `build/job_<id>/render_work/`, joined in batches
  of `RENDER_JOIN_BATCH` (FFmpeg cannot open hundreds of inputs at once on every
  platform), and the final file is written as `.partial.mp4` and renamed only
  when complete. A crash never leaves a truncated file at the deliverable path.
- Every segment is converted to limited-range `yuv420p` before filtering, so
  full-range JPEG sources no longer produce `yuvj420p` output.
- Long-form segments are cached by a hash of source, duration and grade, so a
  resumed render re-encodes only what changed.
- `xfade` is probed once; builds without it fall back to trimmed hard cuts.

**Root cause (long-form drift).** A chapter is laid out as presenter intro,
title slate, then the visual beats. The slate budget was computed from the whole
chapter length and ignored the presenter intro already placed, so each chapter's
pictures ran longer than its narration by the length of the intro. The error
accumulated: in a four-chapter test the last chapter's pictures arrived 11
seconds after the words they illustrate, and the tail was cut off. The layout is
now computed by `_slate_budget`, the shots of a chapter always sum to its
narration, and `visual_coverage_report.json` records `max_chapter_drift_seconds`.

While a title slate is on screen the channel watermark, the disclaimer and the
duplicate section heading are held back, so nothing is drawn over the slate's
own text. Slates carry a provenance record like every other project graphic.

## 8. Technical QA

`core/quality.inspect_video` selects a Shorts or long-form profile and checks
resolution, codec (H.264/AAC), pixel format, frame rate, audio/video length
difference, black and silent intervals, near-identical frames and caption
coverage. The result is PASS, REVIEW or FAIL with a score, stored in
`quality_report.json` and in the database. A FAIL stops the job.

The source-credit slate appended to a documentary is dark, silent text by
design. Black-frame and silence totals cover the programme body only
(`end_card_seconds` records the slate length). The audio clipping check reads
the peak statistics FFmpeg actually reports; the previous pattern never matched,
so every documentary carried a "could not be completed" warning.

## Database safety

`core/db.py`

- Migrations are additive (`ALTER TABLE ... ADD COLUMN`, new tables) and
  idempotent. No migration drops or rewrites user rows.
- Before a migration changes anything, a copy is written to
  `<database dir>/backups/` with the SQLite backup API.
- Writes run in `transaction()`, which rolls back on any exception.
- `update_video` accepts only known column names.
- `integrity_check()` is exposed through `run.py doctor`.
- Formats are normalised on write (`long` → `longform`), which is what allows
  the documentary release gate to recognise scheduled documentaries.
- Under `pytest` or `python -m unittest` the default database is a temporary
  file, so tests cannot migrate or modify `db/studio.sqlite`. CI asserts this
  with `git diff --exit-code -- db/studio.sqlite`.

Restore: stop the process, copy the newest file from `db/backups/` over
`db/studio.sqlite`, run `python run.py doctor`.

The existing six rows in `db/studio.sqlite` are unchanged. Their
`privacy_status` reads `unlisted` because that was the old column default; it is
not a statement about the videos on YouTube. New rows are written as `private`
and updated from the value YouTube reports after upload.

## Publishing safety

`core/uploader.py`, `core/publish.py`, `core/release_gate.py`

1. **Channel lock.** Before the first write the uploader calls
   `channels.list(mine=True)` and compares the result with `YT_CHANNEL_ID`
   (preferred) or `YT_CHANNEL_HANDLE` (default `@engineeringphysics-n5g`). A
   mismatch raises `ChannelMismatchError` and nothing is uploaded.
2. **Private by default.** `upload()` sends `privacyStatus: private` with no
   `publishAt` unless `authorized=True` is passed by a confirmed human action or
   `ALLOW_PUBLIC_PUBLISH=1` is set. `PRIVACY=public` and `PUBLISH_AT_IST` in the
   environment are ignored without that authorisation. `schedule_video` and
   non-private `set_video_privacy` raise `PublicPublishNotAuthorized`.
3. **Release gate.** `release_gate.assert_uploadable` requires: the video file,
   a finished job, technical QA not FAIL, a quality report for this file, no
   fallback-visual block, content QA not FAIL, documentary certification for
   long-form, and source verification for news. The report is saved as
   `release_gate.json`.
4. **Idempotency.** The description carries a stable marker. If an upload with
   that marker already exists (an earlier attempt that crashed before
   bookkeeping), its id is reused and nothing is uploaded twice.
5. **Read-back.** After upload the stored privacy is read from the API. If it
   is not private and public publishing is not authorised, it is set back to
   private and the operator is notified.
6. **Bookkeeping.** The ledger and database are updated before optional steps
   (thumbnail, playlist, pinned comment), which are best effort and reported as
   warnings.

Making a video public is done in the dashboard: the schedule form requires a
ticked confirmation per video, and the bulk action on the calendar page requires
its own confirmation. Automation never publishes.

## 9. Autonomous daily news

**Root cause.** The scheduled workflow only collected headlines. Selection,
drafting, asset preparation, rendering and upload each needed a person to edit
JSON and dispatch another workflow, so no news Short was produced automatically.

`core/news_auto.py` produces at most one Short per run and fails closed:

| Gate | Rule |
| --- | --- |
| Corroboration | The existing `verify_fact_sources` gate: one Tier-1 primary source, or two independent eligible publishers including a Tier-2 outlet |
| Freshness | Published within the breaking-news window in `config/trusted_sources.json` |
| Sensitivity | Deaths, crime, courts, elections, communal or military conflict, disasters, health scares and similar subjects are left for the human workflow |
| Duplicates | Headline similarity and feed item ids against the ledger and the seen cache |
| Grounding | Spoken attribution to a named publisher; every number must appear in the source text; no sensational wording |
| Upload | Private only |

Publisher identity comes from the feed's `<source url>` element, because
aggregator links are redirects that prove nothing about the outlet.

If no story qualifies the run ends successfully with an informational
notification. The gate corroborates headlines and summaries; it does not read
the articles. The private upload is the human checkpoint before publication.
The existing human-reviewed news workflows are unchanged and remain the route
for sensitive stories.

## 10. Notifications

`core/notify.send` writes to the console, to `output/logs/notifications.jsonl`,
to the GitHub job summary, and to every configured channel
(`TELEGRAM_BOT_TOKEN` with `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL`,
`SLACK_WEBHOOK_URL`, `NOTIFY_WEBHOOK_URL`). Delivery is retried on network
errors, HTTP 429 and 5xx, never raises into the pipeline, and redacts tokens.

Events: build success and failure (with the resume command), upload success,
upload blocked (with the reasons), duplicate skipped, topic queue running low,
news run skipped, privacy corrected after upload.

## 11. Operating the system

```bash
python run.py doctor                 # tools, fonts, database, providers, credentials, privacy mode
python run.py shorts "Why ice floats" --category physics --language hinglish
python run.py long "How the grid stays at 50 Hz" --minutes 12 --subtitle-language english
python run.py status --id 12         # stage-by-stage progress and last error
python run.py resume --id 12         # continue from the failed stage
python run.py upload --id 12 --dry-run   # release gate and metadata, no upload
python run.py upload --id 12         # private upload
python run_daily.py --format shorts  # next unused topic
python scripts/run_daily_news_short.py --upload
```

### Recovery

| Symptom | Action |
| --- | --- |
| Build failed mid-way | `python run.py status --id N`, fix the cause, `python run.py resume --id N` |
| Upload blocked: fallback visuals | Resume later so providers are retried, or review and set `ALLOW_FALLBACK_VISUAL_UPLOAD=1` |
| Upload blocked: duplicate | Intended. Override with `ALLOW_DUPLICATE_UPLOAD=1` only for a deliberate remake |
| `ChannelMismatchError` | Recreate `YT_REFRESH_TOKEN` while signed in to the correct channel, or correct `YT_CHANNEL_ID` |
| Upload crashed after YouTube accepted the file | Run the upload again; the marker finds the existing video |
| Ledger unreadable | The run stops rather than risk duplicates. Restore `state/publish_ledger.json` from the `automation-state` branch |
| Database damaged | Restore the newest file in `db/backups/` |

### Required secrets and variables (GitHub Actions)

| Name | Needed for |
| --- | --- |
| `GEMINI_API_KEY` | Scripts (free tier) |
| `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` | Upload |
| `YT_CHANNEL_ID` (repository variable) | Channel lock by id; recommended |
| `PIXABAY_API_KEY` | Stock footage; recommended |
| `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `CEREBRAS_API_KEY`, `MISTRAL_API_KEY` | Optional script fallbacks |
| `TELEGRAM_BOT_TOKEN` with `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL`, `SLACK_WEBHOOK_URL` | Optional alerts |

All services used are free tiers. `FREE_ONLY_MODE` and the disabled paid video
providers are unchanged.

## 12. Tests

```bash
pip install -r requirements-ci.txt -r requirements-dev.txt
python -m pytest tests -q            # full suite, includes a real FFmpeg render
python -m unittest discover -s tests
```

| File | Covers |
| --- | --- |
| `tests/test_platform_core.py` | Config sandboxing, FFmpeg helpers, fonts, timing, captions, checkpoints, dedupe, content QA, model fallback, migrations, cards |
| `tests/test_publish_safety.py` | Private-only upload, channel lock, markers, release gate, duplicate uploads, read-back |
| `tests/test_automation_flow.py` | Topic queue, scheduler, notifications, autonomous news, state branch, workflow hygiene, health check |
| `tests/test_e2e_shorts_render.py` | A complete Shorts build with real FFmpeg, resume after an injected render failure, release gate |

`validate.yml` now runs pytest as well as unittest. Twenty-two function-style
tests were previously never collected in CI.

## Text-model policy (`core/gemini.py`)

Every Gemini call site (`core/script_engine.py`, `core/news_shorts_writer.py`,
`core/visual_semantics.py`) takes its rules from one module:

| Question | Answer |
|---|---|
| Which models, in which order? | `GEMINI_MODEL`, then `GEMINI_FALLBACK_MODELS` (comma-separated). Unset or empty uses the built-in chain; `none` disables fallbacks. Ids are validated before they reach a URL |
| Invalid or unauthorised key (HTTP 401/403, or 400 `API_KEY_INVALID`) | Stop immediately. Not retried, not tried on another model. The message names the `GEMINI_API_KEY` secret |
| Unknown or retired model (404) | Skip to the next model without waiting |
| Rate limit (429) and server errors (500/502/503/504), timeouts | Bounded retries with jittered backoff under a hard cap; `Retry-After` is honoured (seconds or HTTP date), then the next model |
| Malformed response | Treated as a failed draft; nothing unparsed is saved. JSON repair is limited to code fences, trailing commas and bare source ids |
| Which model answered? | Recorded in the script (`generation` block) and appended to `output/logs/llm_usage.jsonl` without prompts or keys |

`python run.py doctor` reports the configured chain and flags invalid ids.

## Optional narration providers (`core/voice.py`)

`edge-tts` is loaded when speech is synthesised, not when the module is imported. Code that only
selects a voice, and workflows that never render audio, do not need the package. A missing
provider raises `VoiceProviderUnavailable` with the install command and the cascade continues to
the offline voice; the reason is written to `narration_synthesis_report.json`.

