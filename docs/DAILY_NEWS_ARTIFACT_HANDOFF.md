# Daily news Short — GitHub Actions artifact handoff

This connects the news research, visual review, and final render stages without committing generated media into the repository.

## Stage 1 — prepare visuals

1. Run **Actions → Daily news Shorts — research queue**.
2. Open its `daily-news-candidates-RUN_ID` artifact and choose a story.
3. Independently verify the original sources. Prepare `reviewed_story.json` with `review_status: "approved"`, non-empty `sources`, and non-empty `verified_facts`.
4. Prepare `shorts_script_draft.json` with 4–6 scenes, source IDs, narration, captions, and visual direction. Keep `editorial_status: "draft_requires_human_script_and_visual_review"`.
5. Commit those two reviewed JSON inputs to the repository, then run **Actions → Daily news Short — prepare visual review artifact** with their repository-relative paths.
6. Inspect the uploaded `daily-news-assets-RUN_ID` artifact. It contains `production_candidate.json`, `reviewed_evidence_packet.json`, `asset_review_manifest.json`, each scene asset, and its `.source.json` provenance sidecar.

The asset-preparation workflow never renders or uploads. It can fail if no sufficiently relevant visual can be sourced/generated; it must not silently substitute unrelated filler.

## Stage 2 — approve the exact artifact and render

1. Review every scene asset, the evidence packet, script, captions, provenance, licence terms, and AI disclosures.
2. Create/commit an approved script JSON. It must have `editorial_status: "approved_for_production"`, 4–6 scenes, and preserve each scene's narration and `source_ids` from the prepared draft. Human editorial changes to narration require a new asset-preparation run so the visual match can be reviewed again.
3. Create/commit an approval JSON for the exact asset-preparation run. Example:

```json
{
  "status": "approved",
  "asset_review_run_id": "1234567890",
  "human_approvals": {
    "editorial": "approved",
    "factual": "approved",
    "visual": "approved",
    "audio": "approved",
    "rights": "approved",
    "disclosure": "approved"
  },
  "asset_reviews": [
    {"scene": 1, "status": "approved", "terms_reviewed": true},
    {"scene": 2, "status": "approved", "terms_reviewed": true},
    {"scene": 3, "status": "approved", "terms_reviewed": true},
    {"scene": 4, "status": "approved", "terms_reviewed": true}
  ]
}
```

4. Run **Actions → Daily news Short — artifact handoff render**. Supply the approved script path, approval path, and exact asset workflow run ID. The workflow derives the artifact name as `daily-news-assets-RUN_ID` to avoid a mismatched artifact selection.
5. The workflow downloads that artifact using the repository Actions read permission, verifies the run ID and all six sign-offs, attaches the assets and provenance sidecars, renders, runs QA, and uploads `daily-news-short-review-RUN_ID`.

## Safety and limitations

- Both workflows are manually dispatched; the scheduled RSS job remains discovery-only.
- No YouTube upload or publication is attempted. The resulting shared review package must remain `REVIEW_REQUIRED`.
- The approval file is an explicit operator attestation. It must include one asset_reviews entry per scene; AI-generated assets require terms_reviewed=true. GitHub does not independently establish that the human actually inspected the assets or licence terms.
- Asset artifacts expire after 14 days. Run the render handoff before expiry.
- The current renderer does not add a 3D presenter. The review checklist flags this for the reviewer.
- A passing code/test run is not proof of a successful real MP4 render or professional visual quality. Inspect the final artifact and play the MP4 before any later upload decision.
