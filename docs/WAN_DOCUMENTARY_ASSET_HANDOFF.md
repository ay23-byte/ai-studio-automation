# Importing Wan2.1 scenes into a documentary job

The Kaggle Wan2.1 worker returns generated MP4 files and `scene_manifest.json`
inside the `wan21-generated-clips` GitHub Actions artifact. Import those outputs
into a review package with:

```bash
python scripts/import_wan_assets.py \
  --manifest path/to/scene_manifest.json \
  --clips-dir path/to/downloaded/wan21-generated-clips \
  --output-dir build/documentary_job/media/wan
```

The importer copies each clip and writes `wan_assets.json` with its scene ID,
prompt, dimensions, duration, SHA-256, and AI-generated provenance. It rejects
path traversal, duplicate scene IDs, missing clips and undersized files.

**Safety boundary:** imported clips are always marked AI-generated,
`ai_reconstruction`, `pending_human_review`, and not publication-ready.
The importer does not invent a source URL or third-party licence, does not
mark visual/licence review complete, and does not modify the documentary
timeline or trigger rendering. Editors must inspect the actual frames, add
disclosure, and explicitly map approved clips into the storyboard/EDL.
This is an asset handoff utility, not an automatic publication integration.
