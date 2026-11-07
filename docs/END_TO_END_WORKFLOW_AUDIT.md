# End-to-end documentary workflow audit

The deterministic audit module checks the hand-offs between research, storyboard,
footage, presenter clips and final export. It creates a JSON report; it does not
claim to independently establish factual truth or legal rights.

## Run

Create a JSON input file with this shape (all fields are optional; empty sections
are reported honestly):

```json
{
  "research": {
    "sources": [
      {"id": "src1", "title": "Source title", "publisher": "Publisher",
       "url": "https://example.org/article", "published_at": "2026-01-01"}
    ],
    "claims": [
      {"id": "claim1", "text": "A factual claim", "status": "needs_verification",
       "source_ids": ["src1"], "evidence": "Relevant excerpt from the source"}
    ]
  },
  "scenes": [
    {"scene_number": 1, "narration": "Exact narration anchor",
     "visual_prompt": "Visual description", "start_seconds": 0, "end_seconds": 12}
  ],
  "assets": [
    {"scene_number": 1, "provider": "Wikimedia Commons",
     "path": "media/scene_01.mp4", "page_url": "https://commons.wikimedia.org/wiki/File:...",
     "licence": "CC BY 4.0", "licence_reviewed": false}
  ],
  "chapters": [
    {"chapter": 1, "narration_duration_seconds": 120,
     "presenter_duration_seconds": 119.8}
  ]
}
```

Run metadata/storyboard/media/presenter checks:

```bash
python scripts/audit_documentary.py --input documentary_audit_input.json
```

Include final MP4 and optional subtitles to run the existing FFmpeg/FFprobe quality
inspection as well:

```bash
python scripts/audit_documentary.py --input documentary_audit_input.json \
  --video output/final_documentary.mp4 --subtitles output/subtitles.ass
```

The report is written to `output/workflow_audit.json`. Exit status is non-zero
when deterministic errors are found. Warnings still require editorial review.

## What the checks mean

- **Research:** source metadata and claim-to-source evidence links are checked.
  A URL or AI-generated summary is not proof. Claims remain pending editor review;
  unsupported claims are marked `needs_verification`.
- **Storyboard:** narration anchors, visual intent, valid timestamps, and scene order
  are checked. It cannot judge semantic relevance of a video frame to spoken words.
- **Footage:** scene coverage, local file existence, provenance and licence-review
  flags are checked. The licence flag is a human attestation, not an automatic legal
  determination. Review the asset page and terms before publishing.
- **Presenter:** chapter and presenter durations are compared. Equal duration does
  not prove lip-sync. The report always states that lip-sync is not independently
  verified.
- **Final export:** the existing `core.quality.inspect_video` checks streams,
  resolution, black intervals, near-silence, repeated sampled frames and subtitles.

## Release gate

Do not publish until an editor has checked factual evidence, asset rights, scene
relevance, subtitle spelling, voice quality, presenter identity/consent and the
rendered video. The report is an assistant to review, not a replacement for it.


## Discovery and rights gates

Live provider discovery must be run with the actual documentary topic. A provider
returning zero candidates is reported as `no_eligible_results`; the overall check
fails when the combined eligible-candidate count is below the configured minimum.
This is a discovery smoke test only: it does not prove that a clip can be downloaded,
inserted into the final timeline, or cleared for publication.

Creative Commons labels are normalized before matching, including hyphenated
`CC BY-SA` labels. A recognized licence label remains only a preliminary
eligibility signal. The exact asset page, licence URL, attribution, restrictions,
third-party material, and rendered frames still require human review.

Search relevance is scored against the focused scene search query/keywords, not
the entire prose visual prompt. The prompt remains provenance context; unrelated
words in a long prompt must not dilute a relevant metadata match.
