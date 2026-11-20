# Production v2 — isolated implementation report (Milestones 2–9)

**Date:** 2026-11-19  
**Branch:** `arena/3ce65062-ai-studio-automation` (same PR as the Milestone 1 audit: #262)  
**Baseline:** `bdb6f82180a22e5ad7532372c3cbd1dc531d95c9`  
**Production behaviour changes:** none. No protected workflow, schedule, trigger, secret name,
privacy setting, channel target or existing upload path was modified.

> **Visual integration update:** an opt-in reviewed Commons file adapter and a single
> conceptual Manim wave and Blender orbit renderer have been added on the current branch.
> Real Manim and Blender render/probe tests passed in GitHub Actions run 44716249833;
> live Commons retrieval remains unverified.
> See [visual asset instructions](VISUAL_ASSETS.md).

This report continues the work requested in the master engineering brief after the
[Milestone 1 audit](../audit/MILESTONE1_REPOSITORY_AUDIT.md). Everything below is additive and
opt-in under `src/production_v2/`. Existing workflows still use their current paths.

---

## 1. What was built

```
src/production_v2/
  config.py           safe defaults, hard private/channel constants, per-format rules
  errors.py           stable failure taxonomy for machine-readable reports
  logging.py          JSON-line logs with credential redaction
  retry.py            bounded exponential backoff
  deps.py             FFmpeg/FFprobe/Python/font preflight
  artifacts.py        per-run artifact store, checksums, atomic writes, manifest
  notify.py           stdlib-only failure webhook (no requests dependency)
  checkpoints.py      resumable stage checkpoints
  models.py           typed dossier/script/scene/quality/upload contracts
  research.py         source validation, claim-to-source mapping, bibliography, rejection
  topics.py           topic/story history and conservative similarity dedupe
  script_engine.py    distinct documentary / news / physics script structures
  narration.py        Edge TTS (free), espeak-ng fallback, labelled offline mock
  subtitles.py        English SRT/ASS from final narration, timing/overflow validation
  audio.py            timeline mix, quiet bed, loudness control, clipping/silence checks
  licenses.py         licence policy, rights register, licenses.csv, credits.txt
  visuals.py          labelled synthetic explainer graphics + future free-provider protocol
  ffmpeg_io.py        FFmpeg/FFprobe invocation, fallback probe, frame hashing, blackdetect
  editor.py           scene rendering, concat, narration mux, English subtitle track
  qa.py               seven-field decision, technical/visual/audio/content/rights checks
  publishing.py       private-only wrapper around the existing core.uploader
  pipeline.py         end-to-end orchestration for all three content types
  cli.py              opt-in `doctor`, `research`, `render`, `demo`, `publish-dry-run`
```

`tests/test_v2_*.py` adds 65 tests covering all 15 test categories in the brief with mocked
external APIs. `scripts/audit/generate_protected_manifest.py` now also hashes the new v2 modules,
so later changes to them require a deliberate manifest update.

---

## 2. Milestone status

| Milestone | Status | Evidence |
|---|---|---|
| 1 — Protection and audit | **Complete** | `docs/audit/MILESTONE1_REPOSITORY_AUDIT.md`, 38 frozen files, 35 workflows inventoried |
| 2 — Reliability foundation | **Complete (isolated)** | deps/retry/logging/notify/checkpoints/artifact integrity modules + 14 reliability tests |
| 3 — Research and fact-checking | **Complete (isolated)** | dossier validation, source tiers, claim-to-source mapping, bibliography, topic dedupe + 15 tests |
| 4 — Narration and subtitles | **Complete (isolated)** | free adapter chain, English-only SRT/ASS from final narration, audio checks + 15 tests |
| 5 — Documentary v2 | **Offline test render complete** | 1920×1080 H.264/AAC demo documentary rendered and inspected; opt-in Commons and two renderer examples exist; real Manim/Blender smoke tests pass, Commons live verification remains outstanding |
| 6 — News Shorts v2 | **Offline test render complete** | 1080×1920 source-attributed script, labelled news graphics, 27 s render |
| 7 — Physics Shorts v2 | **Offline test render complete** | 1080×1920 concept script, animated diagram graphics, 26 s render |
| 8 — Private publishing | **Complete with fake uploader** | private-only adapter, channel check, marker idempotency, read-back verification, all failure states tested; **no live upload performed** |
| 9 — Production readiness | **Partially complete** | full offline suite + real-FFmpeg renders passed; live provider, real narration, real YouTube upload and human video inspection remain outstanding |

### Honest limitation

Milestones 5–7 are **not** a claim that professional documentary quality has been achieved. The
renderer produces structurally correct, purpose-labelled illustrative explainer videos with
motion, narration, subtitles, credits and QA evidence. A reviewed-file Wikimedia Commons adapter, a conceptual Manim wave and a Blender orbit
are now available opt-in; the animation examples have passed real render/probe tests, while Commons remains live-unverified. NASA assets, additional
scientific animations and lip-sync presenters remain follow-on work.

Milestone 9 cannot be fully completed without owner-run credentials and live evidence. This
report does **not** claim a verified YouTube upload or a professional-quality final product.

---

## 3. Safety properties implemented

- `PRIVACY_STATUS` and `CHANNEL_HANDLE` are hard constants: `private`, `@engineeringphysics-n5g`.
- There is no public-upload parameter in v2 publishing; `PV2_UPLOAD` defaults to `0`.
- `PV2_PROVIDER` accepts only `mock` or `free`; any other value fails preflight.
- English subtitles are mandatory; non-English subtitle configuration is rejected.
- Demo sources using `*.invalid` URLs are allowed only when `PV2_DEMO_MODE=1`, and research QA
  **always fails** for demo sources. A demo video can therefore never be uploaded.
- A missing or failed check becomes `FAILED`, `QUALITY_REJECTED` or `UPLOAD_FAILED`, never success.
- Upload idempotency uses a content-hash marker; a retried run reuses the recorded video ID.
- No new workflow file, schedule, cron expression or protected workflow edit was added.

---

## 4. Verification performed

### Local offline end-to-end renders

Using the free `imageio-ffmpeg` wheel as a local FFmpeg binary (not a production dependency) and
the built-in labelled demonstration dossiers:

| Content type | Resolution | Duration | Files | Quality decision |
|---|---|---:|---|---|
| Documentary | 1920×1080, H.264/AAC, 30 fps | 37.0 s | `final.mp4`, SRT/ASS, research/script/asset/QA JSON, `licenses.csv`, `credits.txt`, artifact manifest | technical/visual/audio/subtitles/copyright PASS; research FAIL (demo sources); upload eligibility FAIL |
| News Short | 1080×1920, H.264/AAC, 30 fps | 27.0 s | same set | same result |
| Physics Short | 1080×1920, H.264/AAC, 30 fps | 26.0 s | same set | same result |

A sampled frame from the news render was inspected: the graphic is legible, carries the
`OPENING QUESTION` label and the `ILLUSTRATIVE GRAPHIC — NOT ACTUAL EVENT FOOTAGE` disclosure.
It is a labelled explainer, not documentary footage.

### Tests

- New v2 tests: **65 passed**, 27 subtests passed.
- Full repository suite with real FFmpeg: **1160 passed, 56 skipped, 657 subtests passed**.
- Protected-workflow tests: 12 passed, unchanged workflow hashes and exact schedule set.
- `git diff --exit-code -- db/studio.sqlite`: clean.

Skips are the repository's pre-existing FFmpeg/provider-conditional tests when no binary is
available; with the local FFmpeg wheel configured, the v2 end-to-end render tests ran for real.

---

## 5. How to run

```bash
python -m src.production_v2.cli doctor
python -m src.production_v2.cli demo --content-type documentary
python -m src.production_v2.cli demo --content-type news_short
python -m src.production_v2.cli demo --content-type physics_short
python -m src.production_v2.cli publish-dry-run build/v2/<run-id>
```

Environment (all optional, safe defaults):

- `PV2_UPLOAD=0` — must be set to `1` before any real upload attempt.
- `PV2_PROVIDER=mock|free` — `mock` for offline tests; `free` selects Edge TTS then espeak-ng.
- `FFMPEG_BINARY=/path/to/ffmpeg` — override when FFmpeg is not on PATH.
- `PV2_OUTPUT_DIR`, `PV2_FAILURE_WEBHOOK`, `PV2_TTS_VOICE`, `PV2_PRONUNCIATION_MAP`.

No existing workflow calls these commands. Migration requires explicit owner approval.

---

## 6. Remaining owner actions before production readiness

1. Fix or merge the conflicting release PRs (#256, #258) after independent review.
2. Run one real private upload per content type with the existing authorized YouTube integration
   and record the video ID, timestamp and `privacyStatus=private` read-back.
3. Run live research/footage providers and replace demo sources with strong, dated sources.
4. Replace labelled synthetic graphics with verified licensed archival/real footage where
   appropriate, and add Manim/Blender scientific animations where they improve explanation.
5. Have a human inspect complete rendered videos for narration naturalness, subtitles, visual
   relevance, factual accuracy and rights compliance.
6. Measure documentary runtime/actions minutes against the free compute budget.
7. Decide the disposition of the tracked `db/studio.sqlite` and triage the remaining open PRs.

Until those are done, the correct status is **offline implementation complete, live production
readiness not proven**.
