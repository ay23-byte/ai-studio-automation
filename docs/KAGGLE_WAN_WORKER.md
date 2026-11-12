# Kaggle Wan2.1 GPU worker

This is the standalone remote-worker validation stage for Wan2.1. GitHub Actions submits a prompt to a private Kaggle GPU notebook, waits for inference, downloads the generated MP4, probes its video stream, and stores the output plus a machine-readable validation report as a GitHub Actions artifact.

## One-time GitHub configuration

In **Settings → Secrets and variables → Actions**, add:

- Secret `KAGGLE_USERNAME`: your Kaggle username.
- Secret `KAGGLE_KEY`: the API key from Kaggle Account → API.
- Variable `KAGGLE_KERNEL_SLUG`: a short slug such as `wan21-free-worker` (without the username prefix).

Do not commit Kaggle credentials or put them in the notebook. The workflow checks for missing credentials without printing their values.

## Run a smoke test

Open **Actions → Kaggle Wan2.1 free GPU video worker → Run workflow**. Use either the legacy single `prompt` field or `scene_manifest` JSON. The JSON must be an array of 1-4 objects, each with a non-empty `prompt`; optional `id` and `title` fields label outputs. Example: `[{"id":"opening","title":"Opening aerial","prompt":"Cinematic aerial view of..."},{"id":"lab","title":"Research lab","prompt":"Close-up of scientists..."}]`. Start with 1-2 scenes and settings 480×832, 9 frames and 8 sampling steps. The workflow validates the manifest, runs scenes sequentially on one Kaggle GPU session, validates each MP4 with ffprobe, and uploads clips plus `scene_manifest.json` in the `wan21-generated-clips` artifact.

## Important scope and limitations

- This is a standalone remote-worker validation. It does not yet replace or automatically integrate the local/CI documentary renderer.
- A single run supports up to four sequential scenes. Each scene produces a uniquely named MP4 and a `scene_manifest.json` record with its prompt, dimensions, duration, byte size, and AI-generated provenance. This is a worker-level batch interface, not yet automatic storyboard export/import into the documentary renderer.
- Pipeline integration is a follow-up stage: export approved storyboard prompts, invoke the worker for selected scenes, import returned clips, preserve provenance/licence metadata, then run the existing editorial, factual, and human-review gates. Do not merge this worker into publication flow until those integration tests pass.
- Kaggle controls GPU availability, free quota and session scheduling. Runs can queue, fail when quota is exhausted, or exceed the workflow timeout.
- The notebook uses the public Wan2.1 T2V-1.3B checkpoint and an SDPA fallback intended for constrained GPUs. Compatibility with the current upstream Wan2.1 source and Kaggle image must be proven by an actual Kaggle run; GitHub static checks alone do not prove GPU inference.
- The worker generates illustrative AI scenes, not archival evidence. Record AI-generated scenes separately and disclose them. Human review remains mandatory.
- After inference, the notebook deletes its cloned Wan2.1 source tree and downloaded model checkpoint before the kernel publishes its output. Only the generated MP4 deliverables remain under `/kaggle/working/wan_output`; this prevents multi-gigabyte model weights from being copied into the GitHub Actions artifact.
- The GitHub artifact is a handoff package, not a complete Kaggle runtime snapshot. Keep it limited to generated clips and validation metadata; never upload model weights, caches, or the cloned inference repository.
- This worker does not upload to YouTube or publish anything.
