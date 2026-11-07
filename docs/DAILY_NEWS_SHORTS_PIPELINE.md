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
