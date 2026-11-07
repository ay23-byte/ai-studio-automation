# Daily News Shorts Pipeline

This is a separate research-queue workflow alongside the documentary renderer.

## Schedule
GitHub Actions runs at 08:00 and 19:00 India Standard Time (02:30 and 13:30 UTC). It collects public RSS metadata, deduplicates URLs, and uploads a timestamped candidate queue as a workflow artifact.

## Current milestone: discovery + review queue
- Google News India RSS topic feeds and BBC India/technology RSS metadata discovery.
- RSS/Atom parsing without fetching article bodies.
- URL deduplication and a persistent-state helper for future reviewed-topic suppression.
- Feed-level error reporting and an explicit unverified status.
- Scheduled and manual GitHub Actions workflow.
- Unit tests for metadata parsing, deduplication and seen-state persistence.

Not yet automated: event clustering/ranking, independent verification, claim-level evidence extraction, original 35–58 second script generation, TTS, visuals, captions, rendering, MP4 QA, and YouTube upload.

These stages must not be represented as complete merely because an RSS headline exists. The current job intentionally does not render or upload anything.

## Editorial safety gate
Before a story can become a script, a reviewer must open original sources and confirm it using at least two independent reliable outlets or one authoritative primary source. Record confirmed facts, attributed reports, unknowns, dates and units. Reject unverifiable or unsafe stories. Never present allegations as facts.

## Publishing
The workflow does not access YouTube credentials and cannot publish videos. Keep initial publishing private/manual until YouTube API compliance audit status is confirmed. Do not use article text, publisher images or broadcast footage without rights. Log asset provenance and attribution. Synthetic visuals must be clearly illustrative and must not imitate real people or real news footage.

## Artifacts
Each run uploads latest_candidates.json and a timestamped candidates file for 14 days. Download the artifact from the Actions run to inspect candidates.

## Milestone 2: story clustering, ranking and reviewed script drafts

The discovery queue now groups similar headlines into candidate story clusters and ranks them with an explicitly non-editorial heuristic: 50% recency, 30% distinct publisher domains and 20% headline count. This score is only a triage aid; it is not a reliability score, source verification, or proof of importance. Each cluster includes original links and a human-review checklist.

To draft a Short after reviewing a story, create a JSON packet with:
- `review_status`: exactly `approved`, set only after a human has opened and reviewed the sources.
- `sources`: records containing `source_id`, title, publisher and original URL.
- `verified_facts`: records containing `fact`, `source_ids` and an `evidence_note` describing the inspected passage/data.

Then run:
```bash
python scripts/draft_reviewed_news_short.py reviewed_story.json --output output/daily_news/shorts_script_draft.json
```
The writer refuses packets that are not approved, lack evidence notes, or reference unknown source IDs. It requests a 35–58 second Hindi/Hinglish script and validates scene count, approximate word count and source references. The generated script remains a draft requiring human review. Model output is not itself fact verification.

This workflow still does not render or upload the news Short. Rendering must be connected only after a reviewed script is accepted by the existing video pipeline and its asset provenance/licensing checks pass.


## Milestone 3: deterministic source-tier gate

The script writer now runs a deterministic source verification check before calling Gemini. The rules live in version-controlled `config/trusted_sources.json` and `config/blocklist_sources.json`; unknown, blocked and social domains cannot confirm facts. Breaking-story sources must have publication timestamps within 48 hours; explainers use a seven-day window. A primary tier-1 source is sufficient only as a primary source for its own statement. Otherwise, the packet needs two independent eligible sources, including at least one tier-2 outlet. Repeated wire-origin coverage counts once.

### Required reviewed-packet source metadata

Each `sources` entry must include:
- `source_id`, `url`, and `published` (ISO-8601 timestamp with timezone)
- Optional `origin`, `wire`, `text_snippet`, and `title` to identify syndicated reporting

Each `verified_facts` entry still requires `fact`, `source_ids`, and a human-written `evidence_note`. Set `story_type` to `explainer` only for a genuine explainer; otherwise the 48-hour breaking-news freshness rule applies.

The generated script includes a `source_verification` report showing accepted and rejected source metadata for each fact. A failed fact blocks script generation before any LLM request. This is a provenance and metadata gate—not an automated fact-checker: it cannot prove that a cited article supports the claim, detect coordinated misinformation, or replace human inspection of the cited passage. The human reviewer remains responsible for those checks.

Tests:
```bash
python -m unittest tests.test_news_source_verification -v
```

This milestone does not enable unattended publication. The current daily workflow remains a discovery/review queue; the news writer still requires a human-approved packet, and video QA plus YouTube upload remain separate, gated work.


## Milestone 4: reviewed script → voice → vertical review render

A new local/runner command connects a separately approved script to the existing TTS engine and FFmpeg Shorts renderer:

```bash
python scripts/render_reviewed_news_short.py approved_script.json production_approval.json --output-dir output/daily_news/rendered
```

Before synthesis or rendering, it requires:
- `editorial_status: "approved_for_production"` on the script, separate from the writer's draft status.
- A separate approval JSON containing `{"status":"approved"}`.
- Four to six scenes, narration and captions, and known source IDs on every scene.
- A local image/video file and adjacent `.source.json` provenance record for every scene.
- Real assets to carry an explicit recognized licence and licence URL. AI illustrations must be labelled as AI-generated, have `ai_disclosure: true`, and record `terms_reviewed: true` in their sidecar.

The command synthesizes each scene with the existing voice engine, creates caption subtitles, builds `licenses.csv` and `credits.txt`, then uses the existing 1080×1920/30 fps FFmpeg renderer. Outputs include `review_short.mp4`, `voice.wav`, `captions.ass` and `production_manifest.json`.

**Hard boundary:** this is a review render only. It does not call the YouTube uploader, set privacy, schedule, or publish. Licence metadata is not legal advice or proof of rights; every asset still requires human inspection for exact licence terms, attribution, visual relevance, watermarks and context. Human fact, script, visual, audio and phone playback review remain mandatory. The existing scheduled GitHub workflow remains discovery-only and does not invoke TTS/rendering or upload.

The production approval file is intentionally separate from the generated script so an AI-generated draft cannot approve itself.
