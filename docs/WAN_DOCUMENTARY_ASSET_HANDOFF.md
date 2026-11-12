# Wan2.1 documentary storyboard handoff

The Wan2.1 worker and `scripts/import_wan_assets.py` create AI-generated clips and an
unapproved `wan_assets.json` manifest. This second step attaches selected clips to
existing storyboard shots so they appear in the documentary's visual editorial review.

## 1. Import the worker output

```bash
python scripts/import_wan_assets.py \
  --manifest path/to/scene_manifest.json \
  --clips-dir path/to/downloaded-wan-clips \
  --output-dir build/job/media/wan
```

## 2. Create an explicit mapping

Use the exact `scene_id` values from `wan_assets.json` and exact `shot_id` values
from `visual_review_report.json`. Do not rely on title similarity or guessed ordering.

```json
{
  "mappings": [
    {"scene_id": "opening", "shot_id": "shot_0001"},
    {"scene_id": "factory", "shot_id": "shot_0004"}
  ]
}
```

A scene and a storyboard shot can each appear only once in a mapping file.

## 3. Write a mapped review report

```bash
python scripts/map_wan_assets_to_storyboard.py \
  --assets build/job/media/wan/wan_assets.json \
  --review-report build/job/visual_review_report.json \
  --mapping build/job/wan_storyboard_mapping.json \
  --output build/job/wan_mapped/visual_review_report.json
```

The original review report is never overwritten. The mapped report points the selected
shots at the imported MP4s and records Wan2.1 provenance and the generation prompt.
It remains an AI reconstruction, not archival evidence.

## Review and publication safety

The mapper does not approve visuals, clear rights, certify historical accuracy, render
the documentary, or publish/upload anything. It initializes or refreshes the separate
`visual_review_decisions.json` store using the repository's editorial fingerprint
logic. If media or provenance changes, any previous approval for that shot is invalidated.
Editorial approval, source review, rights review, factual-context review and AI disclosure
remain human responsibilities. An AI-generated clip has no third-party source licence
asserted by this tool; the reviewer must determine the applicable rights and platform
disclosure requirements before publication.
