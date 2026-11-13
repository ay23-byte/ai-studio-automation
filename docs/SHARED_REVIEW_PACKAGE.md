# Shared review-package validator

The first version of the shared review-package contract is defined by:

- `schemas/production-manifest-v1.schema.json`
- `scripts/validate_review_package.py`
- `tests/test_review_package_validator.py`

## Package layout

```text
review-package/
  video.mp4
  captions.srt                 # or captions.ass; manifest names the file
  production_manifest.json
  review_checklist.json
  review_checklist.md
  qa/
    technical.json
    semantic.json
    audio.json
    captions.json
    assets.json
  preview/
    frames/
      frame_*.jpg
  package_summary.json          # written by the validator
```

The manifest uses `schema_version: 1`, `mode: longform | shortform | daily_news`, a topic, caption/video relative paths, and scene-level narration/assets. Every asset records its type, origin (source, AI-generated, or user-provided), provenance, and rights review status.

## Run

```bash
python -m unittest tests.test_review_package_validator
python scripts/validate_review_package.py path/to/review-package
```

The validator uses local `ffprobe` to confirm that the MP4 can be probed, contains one video stream and at least one audio stream, has a positive duration, and matches the expected aspect ratio (16:9 for long-form; 9:16 for both Shorts modes). It checks that referenced files remain inside the package directory, scene assets have provenance, and all five QA reports exist.

## Status semantics

- `FAIL`: structural, path, manifest, MP4, or explicit QA failure.
- `REVIEW_REQUIRED`: technical structure is valid, but a QA report, rights decision, preview, or human sign-off is missing/pending.
- `APPROVED_FOR_UPLOAD`: all required QA reports are PASS, rights are reviewed with licence metadata, preview evidence exists, and all human review fields are explicitly approved.

This is a gate, not an automated quality judge. It does not infer factual correctness, cinematic quality, semantic relevance, copyright ownership, or policy compliance. A real workflow must generate the specified reports and checklist before invoking it. No renderer or YouTube uploader is changed by this PR.

## Human decision export and private release

Standard Shorts and daily-news Shorts use the same manually dispatched workflow:
**Release approved Short (Private)**. It downloads the exact artifact from the selected source run, locates exactly one package, checks that its declared mode matches the selected mode, verifies the MP4 SHA-256, applies the human decisions, reruns the package validator, and only then uploads as PRIVATE with scheduling disabled.

The decision JSON is supplied to the workflow as `review_decisions_json`. Its required shape is:

```json
{
  "schema_version": 1,
  "reviewer": "Your reviewer identity",
  "reviewed_at": "2026-11-13T12:00:00Z",
  "confirmations": {
    "editorial": "approved",
    "factual": "approved",
    "visual": "approved",
    "audio": "approved",
    "rights": "approved",
    "disclosure": "approved"
  },
  "scene_reviews": [
    {"scene_id": "scene-1", "status": "approved", "notes": "Inspected the complete shot against the narration and confirmed relevance."}
  ],
  "asset_reviews": [
    {
      "scene_id": "scene-1",
      "path": "assets/scene_01.jpg",
      "status": "reviewed",
      "license": "Exact licence name",
      "license_url": "https://example.org/licence",
      "attribution": "Required creator/source attribution",
      "notes": "Checked the original source page and exact licence conditions."
    }
  ],
  "qa_reviews": {
    "semantic": {"status": "PASS", "notes": "Compared all sampled visuals with the spoken narration."},
    "audio": {"status": "PASS", "notes": "Listened to the full narration and checked the audio mix."},
    "captions": {"status": "PASS", "notes": "Checked caption wording, spelling, timing and safe areas."},
    "assets": {"status": "PASS", "notes": "Checked source provenance, licence and attribution evidence."}
  }
}
```

Provide one `scene_reviews` entry per scene and one `asset_reviews` entry per asset, using the exact IDs and paths in `production_manifest.json`. Every scene and asset note must contain at least 20 characters. Every asset requires an exact licence name, licence/terms URL and attribution. Unknown provenance cannot be approved. Existing QA FAIL cannot be overridden; missing QA evidence cannot be manually marked PASS. Human notes can resolve only QA reports whose status is REVIEW.

For standard Shorts, the artifact name is `standard-shorts-review-<run_id>`. For daily news, use the exact artifact name `news-review-mp4-issue-<issue_number>` from the selected successful review-render run. The source run ID and artifact name must match.

The workflow requires the exact video SHA-256 and an explicit private-upload confirmation. It uses the existing Engineering Physics channel verification in `core.uploader`, uploads PRIVATE, disables scheduling, and does not support public publishing. A successful render alone is never upload authorization.

**Important:** the workflow is a release mechanism, not a replacement for watching the full video and checking the source item pages and licence terms yourself.
