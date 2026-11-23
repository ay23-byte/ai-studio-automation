# GitHub Actions: start here

Use the **video build** workflow when you want an MP4. The research queue is a separate discovery-only workflow and does not render video.

## Daily news Short — choose the correct workflow

<badge></badge>

| Your goal | Workflow | What it actually produces |
|---|---|---|
| **Generate a video (use this)** | [Daily news Short — VIDEO BUILD FOR REVIEW](../actions/workflows/daily-news-auto-short.yml) | MP4 + review package in artifact `news-short-auto-<run id>`; no YouTube upload |
| Collect possible news topics only | [Research queue — NO VIDEO](../actions/workflows/daily-news-shorts.yml) | JSON candidate queue ZIP only; no script approval, narration, rendering, MP4 or upload |

**Do not run the Research queue expecting a video.** A green research-queue run only means headline collection and its tests completed.

The video-build workflow runs the complete news Short build and creates a review artifact. A successful workflow run should contain the artifact `news-short-auto-<run id>`. If no MP4 is present, inspect the run summary and `run_report.json`; do not treat a green research-only run as a production success.

## Other video builds

| Workflow | Produces |
|---|---|
| [Create a Short](../actions/workflows/make_shorts.yml) | `shorts-review-<run id>` |
| [Create a Documentary](../actions/workflows/make_longform.yml) | `longform-review-<run id>` |

## Review

Download the artifact, open the review dashboard/package, watch the exact MP4 and inspect the source, visual, audio, subtitle and rights checks. Do not approve a video you have not watched.

## Release (private only)

| Workflow | For |
|---|---|
| [Release approved Short (Private)](../actions/workflows/release_shorts_private.yml) | Standard and daily-news Shorts |
| [Release approved documentary (Private)](../actions/workflows/release_documentary_private.yml) | Documentaries |

Enter the build run id, the MP4 SHA-256 from the dashboard and the copied decisions, and tick the confirmation. The video is uploaded as **private** and unscheduled. Publishing is done by you in YouTube Studio.

## Other workflows

Workflows prefixed with **⚙ Internal** are specialist steps, diagnostics, provider checks, experimental renderers, or parts of multi-step handoffs. Do not run them as the normal video-build entry point unless a specific handoff explicitly instructs you to. They do not replace the main video-build workflow. `manual-youtube-private-upload.yml` is retired and uploads nothing.
