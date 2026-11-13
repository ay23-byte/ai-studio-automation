# Production readiness audit

Repository: `ay23-byte/ai-studio-automation` · Channel: Engineering Physics (`@engineeringphysics-n5g`)
First audited commit: `57d7081` (`main` after PR #198) · Audit date: 2026-11-12 ·
Last updated: review-and-release pass (section 14), on top of `2e721fa` (`main` after PR #207).
Sections 1 to 13 describe the state after Phase 2 and are superseded by section 14 where they differ
(release workflows, concurrency groups, the retired upload workflow).

This document records what was **inspected and demonstrated**. Where something could not be
checked it is marked **UNKNOWN** or **NOT VERIFIED** with the reason. It does not replace
`docs/PRODUCTION_READINESS.md` (operator guide from PR #198); it is the evidence base for the
phased hardening plan and is updated at the end of every phase.

Status vocabulary: NOT STARTED · IN PROGRESS · IMPLEMENTED · UNIT TESTED · CI VERIFIED ·
E2E VERIFIED · PRODUCTION VERIFIED · BLOCKED.

> **Headline:** the platform is **not production-ready**. No private YouTube upload has been
> verified through the YouTube API during this audit, the automatic workflows merged in PR #198
> have not yet completed a scheduled run on GitHub Actions, and several providers have never
> been exercised with real credentials from a place where the result could be inspected.

## 1. How the audit was done, and its limits

| Method | Result |
|---|---|
| Read the code, workflows and docs on `main` | Done for the areas listed below; the dashboard pages and the seven open PRs were triaged, not line-reviewed |
| Full test suite locally (Python 3.11, FFmpeg n9.0.2) | 527 passed on `main`; 612 passed with this phase's changes |
| GitHub Actions run metadata through the API | Jobs, steps, conclusions and annotations inspected |
| GitHub Actions **log text** | **Not available** from the audit sandbox (log downloads redirect to a blocked host). Failures were diagnosed by reproducing the workflow's exact install list locally |
| Repository secrets and variables | **UNKNOWN**: listing them returns HTTP 403 for this integration. Whether `GEMINI_API_KEY`, `YT_*` and the others are set cannot be confirmed |
| Live providers (Gemini, Edge TTS, Pixabay, Pollinations, archives, YouTube) | **NOT VERIFIED**: the sandbox has no credentials and cannot reach those hosts |

## 2. Architecture and module inventory

Single Python application (about 16,000 lines in `core/`, 75 modules), driven by CLI entry
points and GitHub Actions, with a Streamlit dashboard. State is SQLite for local job records
plus small JSON files for cross-run state.

| Area | Modules |
|---|---|
| Configuration, health | `core/config.py`, `core/health.py` (`run.py doctor`) |
| Text generation | `core/gemini.py` (shared policy, new in Phase 1), `core/script_engine.py`, `core/llm.py` (free fallback providers), `core/news_shorts_writer.py`, `core/content_qa.py`, `core/seo.py` |
| Topics, duplicates, state | `core/topics.py`, `core/dedupe.py`, `core/jobs.py`, `core/db.py`, `scripts/persist_state.py` |
| News | `core/daily_news_pipeline.py`, `core/news_auto.py`, `core/news_source_verification.py`, `core/news_editorial.py`, `core/news_research.py`, `core/news_short_production.py`, six `core/news_short_*_qa.py` / `news_semantic_qa.py` modules |
| Documentary | `core/pipeline.py`, `core/documentary_storyboard.py`, `core/documentary_edl.py`, `core/research_verification.py`, `core/editorial_review.py`, `core/release_certification.py`, `core/workflow_audit.py` |
| Visuals | `core/footage_sources.py`, `core/images.py`, `core/archival_images.py`, `core/openverse_images.py`, `core/graphic_scenes.py`, `core/explainer_cards.py`, `core/visual_semantics.py`, `core/visual_consistency.py`, `core/cinematic_*.py`, `core/thumbnail.py` |
| Voice and presenter | `core/voice.py`, `core/presenter*.py`, `core/talking_avatar.py`, `core/avatar_clip_generation.py`, `core/news_short_avatar.py` |
| Render and QA | `core/editor_shorts.py`, `core/editor_longform.py`, `core/render_join.py`, `core/ffmpeg_utils.py`, `core/subtitles_export.py`, `core/subtitle_translation.py`, `core/audio_quality.py`, `core/quality.py`, `core/timing.py`, `core/fonts.py` |
| Rights | `core/asset_licensing.py`, `core/asset_manifest.py`, `core/news_short_asset_provenance_qa.py` |
| Publishing | `core/release_gate.py`, `core/publish.py`, `core/uploader.py`, `core/notify.py`, `core/monitoring.py`, `core/scheduler.py`, `core/analytics.py`, `core/performance.py` |
| Optional/experimental video | `core/openvino_video.py`, `core/muapi_video.py` (disabled by policy), `core/wan_timeline_adapter.py`, `kaggle/` |

### Entry points

| Entry point | Purpose |
|---|---|
| `run.py` | `shorts`, `long`, `upload --id [--dry-run]`, `resume --id`, `status --id`, `doctor`, `list`, `demo --format` |
| `run_daily.py` | Scheduled build of the next topic (`--format`, `--upload`, `--upload-latest`) |
| `scripts/run_daily_news_short.py` | Autonomous news Short (`--language`, `--upload`, `--candidates`) |
| `scripts/collect_daily_news.py` | RSS discovery queue (no rendering, no upload) |
| `scripts/draft_reviewed_news_short.py` | Draft from a human-approved packet |
| `scripts/persist_state.py` | Restore/save JSON state on the `automation-state` branch |
| `dashboard.py` + `pages/` (4 pages, about 1,080 lines) | Streamlit dashboard |
| `get_refresh_tokens.py` | One-time local OAuth helper |
| 27 files in `scripts/` | Review packaging, asset registration, validation and upload helpers |

## 3. GitHub Actions workflows (30 files)

| Workflow file | Triggers | Permissions | Timeout (min, per job) | Concurrency group | YouTube secrets | Job summary |
|---|---|---|---|---|---|---|
| `archival-documentary-production-trial.yml` | manual | contents: read | 300 | `archival-documentary-production-trial` | no | no |
| `archival-video-e2e-validation.yml` | manual + pull request | contents: read | 25 | `none` | no | no |
| `avatar-studio-browser.yml` | pull request + push to `feature/free-talking-avatar-studio` | contents: read | 15 | `none` | no | no |
| `chernobyl-documentary-production.yml` | manual | contents: read, actions: read | 300 | `chernobyl-documentary-review` | no | no |
| `daily-news-auto-short.yml` | manual + cron `45 2 * * *`, `45 13 * * *` | contents: write | 45 | `studio-build` | yes | yes (Phase 2) |
| `daily-news-short-artifact-render.yml` | manual | contents: read, actions: read | 90 | `daily-news-short-artifact-render` | no | no |
| `daily-news-short-asset-prep.yml` | manual | contents: read, actions: read | 90 | `daily-news-short-asset-prep` | no | no |
| `daily-news-short-render.yml` | manual | contents: read | 90 | `daily-news-short-review` | no | no |
| `daily-news-shorts.yml` | cron `30 2 * * *`, `30 13 * * *` + manual | contents: read | 15 | `daily-news-shorts-research` | no | yes |
| `documentary-audit.yml` | manual + push to `main` + pull request | contents: read | 10 | `none` | no | no |
| `documentary-e2e-smoke.yml` | manual + pull request | contents: read | 15 | `none` | no | no |
| `documentary-voice-audition.yml` | manual | contents: read | 20 | `none` | no | no |
| `first-documentary-production-trial.yml` | manual + push to `production-trial/disappearing-villages` | contents: read | 180 | `first-documentary-production-trial` | no | no |
| `footage-provider-api-health.yml` | pull request + manual | contents: read | 20 | `none` | no | no |
| `footage-source-validation.yml` | pull request + manual | contents: read | 20 | `none` | no | no |
| `kaggle-wan-video-worker.yml` | manual | contents: read | 360 | `kaggle-wan-worker` | no | no |
| `live-documentary-production-validation.yml` | push to `production-trial/live-footage-e2e-v2` + manual | contents: read | 240 | `live-documentary-production-validation` | no | no |
| `make_longform.yml` | manual | contents: read | 120 | `studio-build` | yes | yes (Phase 2) |
| `make_shorts.yml` | manual + cron `30 3 * * *` | contents: write | 60 | `studio-build` | yes | yes (Phase 2) |
| `manual-live-documentary-e2e.yml` | manual | contents: read | 300 | `manual-live-documentary-validation` | no | no |
| `manual-youtube-private-upload.yml` | manual | contents: read, actions: read | 45 | `manual-youtube-private-upload` | yes | no |
| `news-script-draft.yml` | issue_comment | contents: read, issues: write | 20 | `news-script-draft-issue-<id>` | no | yes (Phase 1) |
| `news-short-one-click.yml` | manual | contents: read, issues: write | 90 | `one-click-news-short-<id>` | no | no |
| `news-short-review-render.yml` | manual | contents: read, actions: read, issues: write | 45 | `news-review-render-<id>` | no | no |
| `news-story-review.yml` | manual | issues: write | 5 | `news-story-review-<story id>` | no | no |
| `news-visual-asset-review.yml` | issue_comment | contents: read, actions: read, issues: write | 50 | `news-visual-assets-issue-<id>` | no | no |
| `openvino-ltx-local-gpu.yml` | manual | contents: read | 360 | `openvino-ltx-local-gpu` | no | no |
| `standard-shorts-review.yml` | manual | contents: read | 90 | `standard-shorts-review` | no | no |
| `validate.yml` | push to `main` + pull request | contents: read | 20/10/30 | `validate-<id>-<id>` | no | no |
| `wan21-documentary-rerender.yml` | manual | contents: read, actions: read | 180 | `wan21-documentary-rerender-<id>` | no | no |

Observations:

* **Job summaries.** The three workflows that build videos for the channel
  (`make_shorts.yml`, `daily-news-auto-short.yml`, `make_longform.yml`) now end with a job
  summary and a `run_report.json` (Phase 2, `core/run_report.py`), in addition to
  `daily-news-shorts.yml` and `news-script-draft.yml` from Phase 1. The summary has been
  produced from real sandbox renders; **no run of these three workflows on Actions has been
  observed yet**, so the summary on GitHub itself is NOT VERIFIED. The manual review and trial
  workflows have no summary.
* `contents: write` exists only on `make_shorts.yml` and `daily-news-auto-short.yml`, and only
  to push JSON to the `automation-state` branch. A test asserts no workflow pushes to `main`.
* `make_shorts.yml`, `make_longform.yml` and `daily-news-auto-short.yml` share the
  `studio-build` concurrency group, so two builds never render at once.
* Every job in every workflow now has `timeout-minutes` (a test enforces it).
  `news-story-review.yml` gained a 5-minute timeout and a per-story concurrency group in Phase 2.
* `daily-news-shorts.yml` (collect only) runs 15 minutes before `daily-news-auto-short.yml`
  (which collects again itself). The overlap is harmless but redundant.
* Thirty workflows is more than the product needs. Many are one-off trials
  (`chernobyl-…`, `first-documentary-production-trial`, `wan21-…`, `kaggle-…`,
  `openvino-…`). None is deleted here: removal needs your explicit authorization.
* **Action versions (Phase 2).** GitHub was already forcing the Node 20 actions onto Node 24
  (annotation on run 43319210500). All hosted workflows now use the Node 24 majors:
  `checkout@v7`, `setup-python@v7`, `upload-artifact@v7`, `download-artifact@v8`,
  `github-script@v9`, `setup-node@v7`, `cache@v6`. Release notes were read for each major;
  the one change that affected this repository is that checkout v6+ no longer stores its
  credential in `.git/config`, which would have broken the push to the `automation-state`
  branch. `scripts/persist_state.py` was fixed first (see `docs/ARCHITECTURE.md`).
  `openvino-ltx-local-gpu.yml` runs on a self-hosted Windows runner whose version cannot be
  checked from here, so it stays on the Node 20 majors. Actions used only by manual or
  issue-triggered workflows (`download-artifact`, `github-script`, `cache`) are **not exercised
  by pull-request checks**; their upgrade is NOT VERIFIED until those workflows run.
  `FedericoCarboni/setup-ffmpeg@v3` (one use, `documentary-e2e-smoke.yml`) still targets
  Node 20 and has no newer release; GitHub runs it on Node 24 with a warning.
  The notice that `ubuntu-latest` is moving to Ubuntu 26 is not acted on.
* **Model ids (Phase 2).** No workflow file contains a Gemini model id any more. Ten
  workflows read repository variables (`GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS`, and the
  `…_LONGFORM` pair for the three documentary workflows); empty values select the built-in
  chains in `core/gemini.py`. The documentary workflows keep their previous lite-model-first
  order through `GEMINI_PROFILE=longform`. Whether any of these variables is set in the
  repository is UNKNOWN (variables cannot be listed from the sandbox).

## 4. Providers

| Provider | Use | Cost | Evidence it works |
|---|---|---|---|
| Google Gemini | Scripts, news drafts, visual check | Free tier | One successful news draft on Actions (run 42388140366, 2026-11-07). Three failures on 2026-11-12, cause unknown (section 8) |
| Groq, Cerebras, OpenRouter, Mistral, GitHub Models, Ollama | Fallback text providers (`core/llm.py`) | Free tiers / local | UNIT TESTED with mocks; never run with real keys in this audit |
| Edge TTS | Narration | Free, unofficial Microsoft endpoint, no SLA | NOT VERIFIED in this audit (host unreachable from sandbox) |
| espeak-ng | Offline narration fallback | Free | Used in sandbox renders; robotic, lands QA on REVIEW |
| Chatterbox | Optional local narration | Free, heavy | NOT VERIFIED |
| Pixabay, Coverr, DVIDS, NARA, Europeana, NASA, Openverse, Wikimedia, Internet Archive, Library of Congress | Footage and images | Free (keys for some) | Two PR validation workflows pass; live results NOT VERIFIED in this audit |
| Pollinations | Generated images | Free | NOT VERIFIED |
| Cloudflare Workers AI | Images | Disabled in code by the zero-paid policy; secrets still passed by 9 workflows | Not applicable |
| **Replicate** | Optional text-to-video adapter in `core/images.py` (`generate_ai_video`). It runs only when `FREE_ONLY_MODE` is off **and** `AI_VIDEO_ENABLED=1` **and** both `REPLICATE_API_TOKEN` and `REPLICATE_VIDEO_MODEL` are set. Every workflow that sets `AI_VIDEO_ENABLED` sets it to `0`. `news-short-one-click.yml` passes the token to one step that also sets `FREE_ONLY_MODE=1`, so the adapter cannot run there | **Paid** | Off by default and blocked in every workflow as configured. *Correction (Phase 2): the Phase 1 version of this row said no Python code reads the token and called it a dead reference. That was wrong; nothing was removed.* Passing the secret to a step that cannot use it is unnecessary and could be dropped with the owner's approval |
| MuAPI | AI b-roll | Paid; disabled permanently in code | Not applicable |
| Pexels | — | — | Excluded by instruction; not used |
| YouTube Data API | Upload, channel check, privacy read-back | Free quota | UNIT TESTED with mocks; **never verified against the real API in this audit** |

## 5. Secrets and environment variables

| Secret referenced in workflows | Workflows using it |
|---|---|
| `CEREBRAS_API_KEY` | 2 |
| `CLOUDFLARE_ACCOUNT_ID` | 9 |
| `CLOUDFLARE_API_TOKEN` | 9 |
| `COVERR_API_KEY` | 11 |
| `DISCORD_WEBHOOK_URL` | 2 |
| `DVIDS_API_KEY` | 9 |
| `EUROPEANA_API_KEY` | 10 |
| `GEMINI_API_KEY` | 11 |
| `GROQ_API_KEY` | 2 |
| `KAGGLE_API_TOKEN` | 1 |
| `KAGGLE_KEY` | 1 |
| `KAGGLE_USERNAME` | 1 |
| `MISTRAL_API_KEY` | 2 |
| `NARA_API_KEY` | 11 |
| `NASA_API_KEY` | 1 |
| `OPENROUTER_API_KEY` | 2 |
| `PIXABAY_API_KEY` | 13 |
| `REPLICATE_API_TOKEN` | 1 |
| `SLACK_WEBHOOK_URL` | 2 |
| `TELEGRAM_BOT_TOKEN` | 3 |
| `TELEGRAM_CHAT_ID` | 3 |
| `YT_CLIENT_ID` | 4 |
| `YT_CLIENT_SECRET` | 4 |
| `YT_REFRESH_TOKEN` | 4 |

Whether any of these is actually configured is **UNKNOWN** (section 1). The code reads about
114 distinct environment variables; the operator-facing ones are documented in `.env.example`
and `docs/PRODUCTION_READINESS.md`. Repository **variables** read by workflows:
`YT_CHANNEL_ID`, `GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS`, `GEMINI_MODEL_LONGFORM`,
`GEMINI_FALLBACK_MODELS_LONGFORM`, `BUILD_RECOVERY_ATTEMPTS`, `REPLICATE_VIDEO_MODEL`; all are
optional. A dedicated `docs/GITHUB_SECRETS.md` is still to be written
(Phase 7). No secret value is stored in the repository; `core/llm.redact` and the Gemini usage
log never record keys, and tests assert this for the new code paths.

## 6. Database and state

| Item | Finding |
|---|---|
| Local job store | SQLite (`core/db.py`), schema version 3, additive migrations that take a backup first, `busy_timeout` 30 s, transactions with rollback, optional WAL |
| Cross-run state | JSON only (`publish_ledger.json`, topic queue, seen-story caches) merged onto the orphan branch `automation-state` by `scripts/persist_state.py`; never pushed to `main` |
| **`automation-state` branch** | **Does not exist on GitHub yet.** No scheduled production run has completed since PR #198, so cross-run duplicate prevention is NOT VERIFIED live |
| **`db/studio.sqlite` is tracked in Git** | It holds six historical job rows (ids 1–4 carry YouTube ids). CI fails if a test modifies it. The brief says a live SQLite file must not be committed; the same brief forbids deleting your data without authorization. **Decision needed from you:** export the six rows to JSON on the state branch and untrack the file, or keep it as a frozen record |
| Concurrency | New tests: six parallel writers lose no rows; a failed transaction leaves nothing behind; eight concurrent claims of one topic produce one ledger row |
| Backup and recovery | New tests: a backup restores a destroyed database; backups never overwrite each other; pruning never touches the live file |
| Git hygiene | New tests: SQLite side files and backups are ignored; no workflow stages or commits a database or media file |

## 7. Tests

* 96 test modules. `pytest tests -q`: **683 passed** locally after Phase 2 (612 after Phase 1,
  527 before it).
* `validate.yml` runs compileall, unittest, pytest, a tracked-database check, an offline Shorts
  render with real FFmpeg, and (new) the news tests in the collect workflow's minimal environment.

Gaps:

| Gap | Phase |
|---|---|
| No test renders a full documentary; long-form is covered by unit tests and a smoke workflow only | 3 |
| No fixed-packet news Short end-to-end test producing an MP4 | 5 |
| YouTube upload, channel identity and privacy read-back are tested only with mocks | 6 |
| The dashboard (`dashboard.py`, `pages/`) has no automated tests: no test module imports it | 7 |
| `issue_comment` workflows cannot be exercised from a pull request; they run from `main` only | — |
| Visual quality is judged by heuristics; no test certifies that a video looks right | — |

## 8. Failing workflows and root causes

| Run | Workflow | Root cause | Status |
|---|---|---|---|
| 43305710825 (schedule, 2026-11-12) | Daily news Shorts — research queue | `core/voice.py` imported `edge_tts` at module level. The workflow installs only `requests` and `Pillow`; its test glob `test_*news*.py` collects `tests/test_news_language_voice.py`, which imports `core.voice` only to call `pick_voice`. Import failed with `ModuleNotFoundError`. **Collection itself had succeeded**; the artifact was not uploaded because the test step failed first. Reproduced locally with the same two packages: 109 tests, 1 error | Fixed in this phase; re-run pending merge |
| 43312611000 (push to main) | validate | No failure. Both jobs (`unit-tests`, `ffmpeg-smoke`) succeeded | CI VERIFIED |
| 43220778290, 43264312306, 43266538505 (2026-11-12) | News script draft (issue #46, `/approve-script`) | **UNKNOWN.** The step ran for 631 s, 281 s and 196 s before exiting 1, so it failed after the source gate. Replaying the issue's packet through `verify_packet` locally passes. That leaves a Gemini failure across all models or three drafts rejected by the script validators; the logs that would tell which are not reachable from the audit sandbox. All three ran on commits older than PR #198 | Diagnostics fixed in this phase: the next failure states its category on the issue |

## 9. Branches and open pull requests

The remote has 194 branches. They were counted, not triaged; many look like feature branches
whose pull requests are already merged. Deleting any of them needs your authorization.

| PR | Title | Mergeable | Assessment (triage, not a line review) |
|---|---|---|---|
| #154 | Automatically upload completed documentaries as Private | Conflicts | Adds a direct upload step to `make_longform.yml` and an `--automatic-private-upload` switch that skips the human-review flag. PR #198 introduced one guarded publish path (`core/publish.py`: release gate, duplicate ledger, privacy read-back). Merging #154 as written would add a second path that bypasses the ledger. Rework on top of `publish_job` in Phase 6 |
| #87 | Make long-form Gemini script generation more resilient | Conflicts | Overlaps with the model-fallback work in #198 and this phase. Compare in Phase 3; likely superseded |
| #86 | Fix disappearing-villages visual beat regression | Mergeable | Small `core/pipeline.py` fix (ignore empty storyboard entries) plus artifact-path narrowing. Looks useful; review and test in Phase 3 |
| #37 | Ranked news clusters and evidence-gated Shorts drafts | Conflicts | Main already produces ranked story clusters and enforces the source gate. Possibly superseded; compare in Phase 5 |
| #28 | ci: trigger first documentary production trial | Conflicts | 2,435 added lines across 17 files on a trial branch. Not reviewed yet; compare with main in Phase 3 |
| #10 | Add more free real documentary footage sources | Conflicts | Opened 2026-11-06; main has gained several footage providers since. Possibly superseded; compare in Phase 3 |
| #2 | Optional Intel OpenVINO presenter backend | Conflicts | Optional, hardware-specific. Low priority |

No PR is closed by this phase; closing them is your decision.

## 10. Feature matrix

| Feature | Implemented | Unit tested | End-to-end evidence | Production status |
|---|---|---|---|---|
| Documentary generation | Yes | Yes | Offline sandbox render only (109 s output, fallback cards, espeak voice, QA "REVIEW") | NOT VERIFIED. Outcomes of your earlier trial runs were not inspected (UNKNOWN) |
| Shorts generation | Yes | Yes | CI VERIFIED offline: `tests/test_e2e_shorts_render.py` renders a real 1080x1920 MP4 and probes it | NOT VERIFIED with real providers |
| News discovery | Yes | Yes | Collection step succeeded in run 43305710825; the job failed afterwards | IN PROGRESS: re-run after this PR |
| News script generation (human-gated) | Yes | Yes (mocked HTTP) | One real draft on Actions, 2026-11-07 (run 42388140366) | Unreliable: 3 failures on 2026-11-12, cause UNKNOWN |
| News video rendering | Yes | Yes | Review MP4s rendered on Actions on 2026-11-07 (runs 42393711095, 42396976888); files not inspected in this audit | NOT VERIFIED |
| Automatic news Short | Yes (PR #198) | Yes | Offline sandbox run only | NOT VERIFIED: never run on Actions |
| Voice generation | Yes | Yes (mocked) | Edge TTS not reachable from the sandbox | UNKNOWN |
| Presenter / avatar | Partly | Partly | None | BLOCKED: portrait ownership is unconfirmed in the rights register. A still portrait must not be described as an animated presenter |
| Footage sourcing | Yes | Yes | PR validation workflows pass | NOT VERIFIED live in this audit |
| Subtitles | Yes | Yes | Burned-in captions in the CI Shorts render; bundled Noto fonts | English/Hindi rendering not visually certified |
| Audio mixing and loudness | Yes | Yes | Sandbox renders | NOT VERIFIED on production output |
| YouTube OAuth | Yes | Mocked | None | UNKNOWN: secrets cannot be listed |
| Private YouTube upload | Yes (gate, duplicate check, privacy read-back) | Mocked | **None** | **NOT VERIFIED through the YouTube API** |
| Scheduling | Yes (three cron workflows) | Workflow hygiene tests | No scheduled run of the new workflows has completed yet | NOT VERIFIED |
| Duplicate prevention | Yes (ledger + DB rows) | Yes, including concurrency | State branch does not exist yet | NOT VERIFIED across runs |
| Dashboard | Yes (Streamlit) | No | Not exercised in this audit | NOT AUDITED (Phase 7) |
| Recovery and retry | Yes (checkpoints, resume, bounded retries; one automatic in-run resume on the two scheduled build workflows since Phase 2) | Yes | Sandbox: a real Shorts build with the render stage forced to crash was resumed by the wrapper, reused the narration checkpoint and finished (job 1, 27.2 s, 1080x1920) | NOT VERIFIED on Actions |
| Job summary and failure report | Yes (Phase 2) | Yes | Generated from two real sandbox renders (built; built with upload blocked by the release gate) | NOT VERIFIED on Actions |

## 11. Missing or incomplete against the brief

* Job summaries exist on the three production build workflows but have not been seen on a real
  Actions run. The manual review-and-upload workflows (`manual-youtube-private-upload.yml`,
  the news review renders) still have none.
* Documentary evidence bundle as named files: `licenses.csv`, `credits.txt`,
  `asset_manifest.json`, `research_manifest.json`, `production_report.json`. Equivalent data
  exists under other names; the mapping and the release check are Phase 3.
* A fixed-packet news Short end-to-end test and a documentary end-to-end test.
* An upload receipt verified against the API and stored as an artifact, exercised for real.
* Operator documents: `SETUP.md`, `GITHUB_SECRETS.md`, `PROVIDERS.md`, `WORKFLOWS.md`,
  `TESTING.md`, `OPERATIONS.md`, `TROUBLESHOOTING.md`, `RELEASE_CHECKLIST.md`.
* The news-draft writer has no non-Gemini fallback provider, and its scientific validators are
  written for one specific Himalayan packet.
* `make_longform.yml` is manual and its uploads do not reach the shared duplicate ledger.

## 12. Risks

| Risk | Severity | Note |
|---|---|---|
| Uploads have never been verified end to end | High | Nothing may be called production-ready until one private upload is read back from the API |
| Edge TTS is an unofficial endpoint | High | If it is blocked from Actions, narration drops to espeak-ng and QA holds the video. No second natural-voice provider is free without a key |
| Free-tier Gemini quota | Medium | Mitigated by model fallback and optional free providers; scheduled runs may still skip a day |
| Tracked SQLite file | Medium | Decision pending (section 6) |
| Workflow sprawl (30 files, 194 branches) | Medium | Hard to tell which paths are supported |
| Rights for presenter portrait | Medium | Blocks presenter shots |
| Action major upgrades not exercised by PR checks | Low | `download-artifact@v8`, `github-script@v9`, `cache@v6` run only in manual or issue-triggered workflows; watch the first run of each |
| Runner image moving to Ubuntu 26 | Low | apt package names (`ffmpeg`, `espeak-ng`, `libraqm0`) may change; not acted on |

## 13. Acceptance checklist

None of the 23 acceptance items in the brief is ticked. Ticking requires demonstrated
evidence; the earliest candidates are "validate passes on main" and "daily news collection
succeeds on schedule", both to be confirmed from Actions runs after this phase merges.

## 14. Review-and-release pass (on top of `2e721fa`)

Scope: one review contract and one private-release path for documentaries, standard Shorts and
daily-news Shorts; repair of the documentary packaging failure; workflow wiring; tests.

### 14.1 Audit matrix

Severity: **Critical** = can publish unreviewed content or the wrong file; **High** = a production
mode cannot complete or a safety gate can be bypassed; **Medium** = reliability or correctness gap
with a workaround; **Low** = clarity and hygiene.

| # | Severity | Finding | Evidence | Status |
|---|---|---|---|---|
| 1 | High | Documentary build failed at "Assemble shared long-form review package" (run 43484926451). `scripts/package_longform_review.py` lazily imported `scripts.build_review_dashboard`; run by path, the repository root is not on `sys.path` | Reproduced with the old code on a real local documentary build: `ModuleNotFoundError: No module named 'scripts'` | **Fixed.** The new packager ran on the same build. A source scan test now fails if any script imports `core`/`scripts` without the path fix |
| 2 | High | 15 scripts imported `core`/`scripts` without the path fix; `--help` passing did not show it because the imports are lazy | AST scan; old `release_review_package.py --help` failed outright | **Fixed and tested** (`tests/test_review_release_e2e.py`, `tests/test_release_plumbing.py`) |
| 3 | High | `make_shorts.yml` and `daily-news-auto-short.yml` produced no review package, so their videos could not go through the release path at all | Workflow files | **Fixed**: both package after the build; not run on Actions |
| 4 | High | Review decisions (v1) named no job, no video hash and no reviewer; one decision file fitted any package | `scripts/apply_review_decisions.py` at `2e721fa` | **Fixed**: decisions v2 are bound to job id, MP4 SHA-256 and manifest fingerprint; v1 is rejected |
| 5 | High | Documentaries used a separate upload path with a single "I reviewed it" tick box (`manual-youtube-private-upload.yml`) | Workflow file | **Retired**: the workflow holds no credentials and exits with a pointer. The stricter documentary gates were carried into the packager |
| 6 | High | A release could use the artifact of a failed or unrelated run; only "numeric id" was checked | `release_shorts_private.yml` at `2e721fa` | **Fixed**: `scripts/verify_source_run.py` (unit tested; the GitHub API call itself is not exercised) |
| 7 | High | Privacy was requested as private but not read back after upload | Release script at `2e721fa` | **Fixed**: privacy, schedule and channel are read back; a mismatch is exit 3. Tested with a fake uploader only |
| 8 | Medium | Duplicate-upload lookup used YouTube search, which lags behind new uploads, so a quick rerun could upload twice | `core/uploader.py` | **Fixed**: the uploads playlist is read first. Fake-API tests only |
| 9 | Medium | Robotic fallback narration and card-slideshow builds were packaged as reviewable | Local Shorts and documentary builds in the sandbox | **Fixed**: such packages are `FAIL`, the build run is red |
| 10 | Medium | The three build workflows shared one concurrency group; a documentary could hold back or displace the scheduled Shorts and news runs | Workflow files | **Fixed**: one group per mode. The state branch push already merges and retries |
| 11 | Medium | `make_shorts.yml` passed YouTube credentials to the preflight step of a build that never uploads | Workflow file | **Fixed**: removed; a test asserts only the two release workflows reference them |
| 12 | Medium | Local uploads (`python run.py upload`, the local dashboard) use `core/publish.py` and `core/release_gate.py`, not the review contract | Code reading | **Open.** Private-only and channel-locked, but without per-item human decisions. Not reachable from Actions |
| 13 | Low | `scripts/create_voice_audition.py` had no argument parser; `--help` started synthesis | Ran it | **Fixed** |
| 14 | Low | Three trial workflows declared `PRIVACY: "unlisted"` (they hold no credentials) | Workflow files | **Fixed**: set to `private` |
| 15 | Low | README said workflows upload as unlisted | README | **Fixed** |
| 16 | Low | Schema existed only for manifest v1 | `schemas/` | **Fixed**: v2 manifest and decisions schemas, validated against real packages in tests |

### 14.2 What was demonstrated

| Check | Result |
|---|---|
| Full suite (`python -m pytest tests -q`) | 808 passed locally; `main` at `2e721fa` had 697 |
| Shorts build in the sandbox (`python run.py demo --format shorts`) | MP4 1080x1920, 27.2 s. Packager: `FAIL` as designed (espeak fallback voice, 4 of 4 fallback cards) |
| Documentary build in the sandbox (`python run.py demo --format long`) | MP4 1920x1080, 107.4 s. Packager: `FAIL` as designed (fallback voice, 12 of 12 cards, no research ledger in demo mode) |
| Package, review, release for all three modes | `tests/test_review_release_e2e.py`: real FFmpeg, packaging run as a subprocess from another directory, fake uploader. Empty decisions refused; complete decisions uploaded once as private; rerun reused the upload; re-encoded MP4 refused |
| Dashboard | Loaded in jsdom: no script injection from hostile text, nothing pre-selected, exported decisions accepted by the contract |

### 14.3 Not verified

- **No real YouTube upload or read-back was performed.** The release path ran only against a fake uploader.
- **No workflow changed here has run on GitHub Actions.** Workflows cannot be dispatched from the audit sandbox (HTTP 403). The release workflows, `actions/download-artifact@v8` with `run-id`, and the packaging steps are untested there. No workflow linter new enough for these files was available offline.
- A package that passes automated QA could not be produced from a sandbox build (no narration provider or footage host is reachable), so the approve-and-release path was exercised with constructed build outputs, not a pipeline-built video.
- The dashboard was not opened from disk in a real browser.
- Real Gemini and fallback script providers, Edge TTS and the footage sources remain unverified, as in section 4.

### 14.4 Decisions left to the owner

- `db/studio.sqlite` is still tracked user data. It was not touched.
- Merged branches, trial workflows and the seven open pull requests (#154, #87, #86, #37, #28, #10, #2) were not deleted or closed.
- Whether local uploads (finding 12) should also require the review contract.

