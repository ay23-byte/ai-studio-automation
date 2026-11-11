# Kaggle Wan2.1 GPU worker

This is the first remote-worker milestone for Wan2.1. GitHub Actions submits a prompt to a private Kaggle GPU notebook, waits for inference, downloads the generated MP4, and stores it as a GitHub Actions artifact.

## One-time GitHub configuration

In **Settings → Secrets and variables → Actions**, add:

- Secret `KAGGLE_USERNAME`: your Kaggle username.
- Secret `KAGGLE_KEY`: the API key from Kaggle Account → API.
- Variable `KAGGLE_KERNEL_SLUG`: a short slug such as `wan21-free-worker` (without the username prefix).

Do not commit Kaggle credentials or put them in the notebook.

## Run a test

Open **Actions → Kaggle Wan2.1 free GPU video worker → Run workflow**. Enter a cinematic prompt. Keep the initial settings for the first test: 480×832, 9 frames and 8 sampling steps.

The workflow pushes a private GPU-enabled notebook, waits for Kaggle, downloads the kernel output, and uploads the MP4 as the `wan21-generated-clips` artifact. Download that artifact from the completed Actions run.

## Runtime and limitations

- Kaggle controls GPU availability, free quota and session scheduling. Runs can queue, fail when quota is exhausted, or exceed the workflow timeout.
- The worker uses the public Wan2.1 T2V-1.3B checkpoint and PyTorch scaled-dot-product attention instead of FlashAttention. This fallback is slower and can use more memory.
- The current notebook generates one scene clip per workflow run. It is an initial remote clip-worker bridge, not yet an automatic replacement for the monolithic `make-longform` render.
- Automatic insertion into the documentary pipeline requires a separate refactor: export storyboard prompts, invoke this worker for selected scenes, import returned clips, then render and run the existing editorial/licensing checks. That must be completed and tested before calling the documentary pipeline integrated.
- This worker does not upload to YouTube or publish anything.
- AI-generated scenes are illustrative, not archival evidence. Review each clip and label AI-generated material appropriately.
