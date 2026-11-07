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