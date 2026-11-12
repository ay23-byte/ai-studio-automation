# Standard Shorts review workflow

Run **Standard Shorts — review render** from GitHub Actions using `workflow_dispatch`.

Provide a JSON object with `title`, optional `hook` and `category`, and a non-empty `scenes` array. Every scene must contain `say` narration; `caption`, `image`, `keywords`, and optional camera fields are passed to the existing renderer. Choose English or Hindi narration and a voice gender.

The workflow installs FFmpeg and the free Edge TTS/Python dependencies, calls the existing `core.pipeline.build_shorts` renderer, checks that the output has one 1080×1920 video stream and an audio stream, and uploads the MP4 plus script, ASS captions, quality report, and shot plan as a 14-day review artifact.

## Safety and current limits

- This workflow renders a review copy only. It does not call the YouTube uploader and does not publish.
- It uses the existing Shorts visual providers. If image generation and relevant footage providers fail for any scene, the renderer stops rather than inserting unrelated filler.
- The workflow is CPU-based and does not request a paid GPU.
- GitHub repository secrets such as `PIXABAY_API_KEY` are optional provider credentials; missing keys may reduce available visual sources.
- A successful run proves the workflow produced a technically probeable review MP4, not that the facts, visual relevance, asset rights, captions, or cinematic quality are approved.
- This first workflow emits a review bundle but does not yet convert the renderer's per-scene assets into the shared `production_manifest.json` contract or invoke `validate_review_package.py`. That integration is a follow-on task. Human inspection remains mandatory.
