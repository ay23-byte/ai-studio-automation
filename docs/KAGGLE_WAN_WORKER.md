# Kaggle Wan2.1 GPU worker

This is the standalone remote-worker validation stage for Wan2.1. GitHub Actions submits a prompt to a private Kaggle GPU notebook, waits for inference, downloads the generated MP4, probes its video stream, and stores the output plus a machine-readable validation report as a GitHub Actions artifact.

## One-time GitHub configuration

In **Settings → Secrets and variables → Actions**, add:

- Secret `KAGGLE_USERNAME`: your Kaggle username.
- Secret `KAGGLE_KEY`: the API key from Kaggle Account → API.
- Variable `KAGGLE_KERNEL_SLUG`: a short slug such as `wan21-free-worker` (without the username prefix).

Do not commit Kaggle credentials or put them in the notebook. The workflow checks for missing credentials without printing their values.

## Run a smoke test

Open **Actions → Kaggle Wan2.1 free GPU video worker → Run workflow**. Use a short, simple prompt and the initial settings: 480×832, 9 frames and 8 sampling steps. The workflow pushes a private GPU-enabled notebook, waits for Kaggle, downloads kernel output, validates MP4 metadata with ffprobe, and uploads the `wan21-generated-clips` artifact.

## Important scope and limitations

- This is a standalone remote-worker validation. It does not yet replace or automatically integrate the local/CI documentary renderer.
- Pipeline integration is a follow-up stage: export approved storyboard prompts, invoke the worker for selected scenes, import returned clips, preserve provenance/licence metadata, then run the existing editorial, factual, and human-review gates. Do not merge this worker into publication flow until those integration tests pass.
- Kaggle controls GPU availability, free quota and session scheduling. Runs can queue, fail when quota is exhausted, or exceed the workflow timeout.
- The notebook uses the public Wan2.1 T2V-1.3B checkpoint and an SDPA fallback intended for constrained GPUs. Compatibility with the current upstream Wan2.1 source and Kaggle image must be proven by an actual Kaggle run; GitHub static checks alone do not prove GPU inference.
- The worker generates illustrative AI scenes, not archival evidence. Record AI-generated scenes separately and disclose them. Human review remains mandatory.
- This worker does not upload to YouTube or publish anything.
