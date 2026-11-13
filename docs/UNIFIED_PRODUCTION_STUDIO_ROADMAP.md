# Unified Production Studio: audit and implementation roadmap

**Audit scope:** repository default branch `main`, inspected 2026-11-12  
**Goal:** one dependable production system with three independent entry points: long-form documentaries, standard Shorts, and daily-news Shorts.

This is an engineering roadmap, not a claim that all workflows are already production-ready. Existing pipelines should be retained and connected incrementally.

## Executive finding

The repository contains substantial generation and editing components, but it does not yet demonstrate one complete, repeatable end-to-end path for all three formats.

The clearest verified gap is daily news: `.github/workflows/daily-news-shorts.yml` runs at 08:00 and 19:00 IST, but it only collects RSS metadata, compiles/tests modules, and uploads a candidate queue. `core/daily_news_pipeline.py` explicitly labels results as discovery metadata, not independently verified stories. `scripts/draft_reviewed_news_short.py` creates a human-review script draft; `scripts/prepare_news_short_assets.py` prepares assets and explicitly does not render or upload. Therefore the scheduled workflow is a research queue, not an automated finished-Short pipeline.

Long-form has a stronger path: documentary workflows invoke `python run.py long`; `core/editor_longform.py` renders 1920x1080 video with motion, transitions and audio; and the Wan2.1 work adds asset import, storyboard mapping, timeline adaptation and a manual rerender workflow. However, the Wan2.1 route still needs a real run with compatible documentary and worker artifacts, correct scene-to-shot mappings, a playable rendered MP4, and human visual inspection. A successful PR test is not that proof.

Shorts has a dedicated vertical renderer in `core/editor_shorts.py` (1080x1920, 30 fps, H.264, AAC, captions, transitions and audio mix) and a fail-closed news QA script in `scripts/qa_news_short.py`. The QA script itself states that without a production manifest semantic and caption alignment cannot be verified, and that human visual review is required. The current audit did not establish a single fresh, successful standard-Short run from topic to review artifact.

## Existing building blocks

| Area | Existing implementation | Current limitation |
|---|---|---|
| Documentary production | `run.py long`, documentary workflows, `core/editor_longform.py` | Full end-to-end reliability and actual footage coverage need run evidence |
| Wan2.1 video | Kaggle worker, import/map/adapter scripts, rerender workflow | Integration requires real artifacts and mapping; visual quality is not proven by import tests |
| Standard Shorts | `core/editor_shorts.py` | Need a reproducible fresh-topic workflow and complete review package |
| News discovery | `core/daily_news_pipeline.py`, scheduled workflow | RSS metadata only; not fact verification |
| News script draft | `scripts/draft_reviewed_news_short.py` | Requires a human-approved evidence packet; draft is not final narration/render |
| News visual preparation | `scripts/prepare_news_short_assets.py` | Explicitly does not render or upload |
| News QA | `scripts/qa_news_short.py` | Needs manifest for semantic/caption/provenance checks; human review remains required |
| YouTube | Existing upload code and credentials | Must remain downstream of a separate explicit approval |

## Target architecture

Keep three production modes separate at the editorial layer, and standardize their outputs at the review-package boundary.

```text
LONG-FORM TOPIC ──> research/script/chapters ──> scene plan ──> footage + AI visuals
                                                           └─> narration/presenter
                                                           └─> long-form edit
                                                           └─> QA ──┐
STANDARD SHORT ──> topic/script ──> scene plan ──> footage + AI visuals
                                               └─> narration/captions
                                               └─> vertical edit
                                               └─> QA ─────────────┤
NEWS SHORT ──────> RSS candidates ──> source verification ──> human story approval
                                                    └─> script approval
                                                    └─> sourced scene plan/assets
                                                    └─> narration/captions/render
                                                    └─> news QA ──┘
                                                                  |
                                             STANDARD REVIEW PACKAGE
                                             MP4 + captions + manifest
                                             provenance/credits + QA reports
                                             preview frames + review checklist
                                                                  |
                                                   HUMAN APPROVAL REQUIRED
                                                                  |
                                            SEPARATE PRIVATE/UNLISTED UPLOAD
                                            No public auto-publish by default
```

## Shared review-package contract

Every completed mode should emit a package directory with:

- `video.mp4`: final encoded output; 16:9 for long-form, 9:16 for Shorts.
- `captions.srt` or `captions.ass`: exported caption track, plus proof it was muxed/burned as intended.
- `production_manifest.json`: topic, mode, duration, dimensions, scenes/shots, narration segments, asset paths, asset types, source IDs/URLs, provider, licence/rights status, AI-generated flags, and generation prompts where applicable.
- `qa/technical.json`: ffprobe-derived stream, codec, resolution, fps, duration and audio checks.
- `qa/semantic.json`: scene-to-narration relevance status; must say `NOT_RUN` rather than silently passing when evidence is missing.
- `qa/audio.json`, `qa/captions.json`, `qa/assets.json`: audio, caption alignment, provenance/rights checks.
- `preview/frames/`: representative sampled frames for human inspection.
- `review_checklist.md`: editorial, factual, visual, audio, rights and disclosure decisions.
- `package_summary.json`: status, blocking errors, warnings, required human reviews, and publication eligibility.

The package status must be one of `FAIL`, `REVIEW_REQUIRED`, or `APPROVED_FOR_UPLOAD`. Technical success alone must never imply editorial approval or publication readiness. Unknown licence status must remain unknown and block upload until resolved.

## Release handoff implementation notes

The long-form documentary build, standard Shorts build, and scheduled daily-news Short workflows are now configured as **review-only** producers. Their build workflows do not call YouTube. Standard Shorts and daily-news Shorts preserve their generated MP4s, reports, and build directories in run-scoped artifacts. Documentary builds use the separate **Release approved documentary (Private)** workflow, which downloads the exact source artifact, verifies the MP4 SHA-256, attaches the exported per-shot human decisions, requires an explicit private-upload confirmation, and then invokes the existing fail-closed documentary uploader.

Important remaining limitation: the generic shared review manifest/dashboard and guarded release workflow for standard Shorts and daily-news Shorts are not yet implemented. Those modes are protected from build-time auto-upload, but a complete human review-to-private-release path for them is still outstanding. Daily-news automatic story selection also remains a distinct editorial risk boundary: only the existing source verification policy can select an auto-eligible story, and the review-only workflow must not be described as human story approval. No public upload or scheduling is enabled by this PR.

## Prioritized implementation sequence

### P0 — Establish a shared contract and fail-closed package validator

1. Define a versioned JSON schema for the manifest and package summary.
2. Add a shared validator that checks file presence, MP4 playability, stream metadata, expected aspect ratio, caption artifact, manifest-to-file references, provenance fields, QA reports and review checklist.
3. Missing semantic QA, missing source/licence metadata, or missing human review must result in `REVIEW_REQUIRED` or `FAIL`, never a green publication status.
4. Add unit tests with valid, missing-file, invalid-MP4, wrong-aspect, unknown-rights and missing-review fixtures.

**Acceptance:** all modes can call the same validator; malformed or incomplete packages cannot be labelled upload-ready.

### P1 — Make standard Shorts reproducible

1. Identify and document the canonical standard-Short entry point and workflow (do not create a competing renderer).
2. Add a manual workflow with topic, language, target duration and review-only mode inputs.
3. Run the existing script/visual/narration pipeline and `core/editor_shorts.py`.
4. Require a production manifest and run technical, caption, semantic, asset-provenance, presenter and audio QA.
5. Upload a review package artifact; do not upload to YouTube.

**Acceptance:** one fresh topic produces a playable 1080x1920 MP4 plus a complete review package; missing footage or semantic manifest cannot silently pass.

### P2 — Complete daily-news Shorts orchestration

1. Keep scheduled RSS discovery as a separate research job.
2. Require a human-selected candidate and a verified evidence packet (two independent reliable sources or one authoritative primary source, publication dates, verified facts, uncertainty labels).
3. Keep script approval and visual approval as explicit human gates.
4. Connect approved packet -> script draft -> scene assets -> narration/captions -> vertical render -> news QA -> shared review package.
5. Preserve source IDs at the scene level; verify asset relevance and provenance. Do not substitute generic stock for a scene-specific news visual without an explicit review warning.
6. Do not auto-publish. Upload only after a separate explicit approval action.

**Acceptance:** daily discovery remains safe and useful; an approved news story can be rendered end-to-end into a review artifact; unverified candidates cannot reach rendering or publishing.

### P3 — Prove long-form and Wan2.1 end-to-end

1. Run a fresh documentary workflow and preserve script, visual review report, scene plan and review package.
2. Run the Kaggle worker with a scene manifest; verify actual generated clips with ffprobe and inspect sample frames.
3. Map only real scene IDs to real storyboard shot IDs.
4. Execute the Wan2.1 rerender workflow and validate the final timeline contains the mapped clips.
5. Validate actual footage coverage, presenter coverage, runtime, audio, captions, source/AI provenance and credits.
6. Inspect the final MP4 visually before marking the trial successful.

**Acceptance:** a real Wan2.1 clip is visible in the final documentary at its intended shot; the complete review package is downloadable; no YouTube upload occurs during this test.

### P4 — One dashboard/dispatcher and guarded upload

1. Add one dispatcher entry point with modes `longform`, `shortform`, and `daily_news`, delegating to existing workflows or shared scripts.
2. Show status and artifact links per run; do not hide partial failures.
3. Keep YouTube upload separate from rendering and require a human approval input or protected environment.
4. Default to private/unlisted. Never publish publicly automatically.
5. Record the exact approved package hash and ensure the uploaded file matches it.

**Acceptance:** the user can select a mode, obtain a review package, approve it, and only then trigger private/unlisted upload.

## Required real-run evidence before declaring production-ready

- At least one fresh, visually inspected long-form MP4.
- At least one fresh, visually inspected standard Short.
- At least one fresh, visually inspected daily-news Short made from an approved evidence packet.
- At least one fresh Wan2.1 clip mapped into a final documentary render.
- QA reports and source/rights manifests for all three formats.
- Proof that no upload happens before approval and that default privacy is private/unlisted.

## Explicit non-goals

- No paid APIs, paid GPU services, or new billing commitments without explicit approval.
- No replacement of existing renderers merely to simplify architecture.
- No public auto-publishing.
- No claims that tests, mocked media, a non-empty MP4, or successful workflow syntax prove cinematic quality.
