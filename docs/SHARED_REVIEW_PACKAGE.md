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
