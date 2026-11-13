# GitHub Actions: start here

Normal production uses a build workflow, a review, and a release workflow.

## 1. Build (never uploads)

| Workflow | Produces |
|---|---|
| [Create a Short](../actions/workflows/make_shorts.yml) | `shorts-review-<run id>` |
| [Create a Documentary](../actions/workflows/make_longform.yml) | `longform-review-<run id>` |
| [Daily news Short](../actions/workflows/daily-news-auto-short.yml) | `news-short-auto-<run id>` |

A green run means a review package is waiting for you. A red run means the video could
not be built or failed automated quality checks; the run summary says why.

## 2. Review

Download the artifact, open `build/job_<id>/shared_review_package/review_dashboard.html`,
watch the video and decide every item. Copy the decisions from the dashboard.

## 3. Release (private only)

| Workflow | For |
|---|---|
| [Release approved Short (Private)](../actions/workflows/release_shorts_private.yml) | Standard and daily-news Shorts |
| [Release approved documentary (Private)](../actions/workflows/release_documentary_private.yml) | Documentaries |

Enter the build run id, the MP4 SHA-256 from the dashboard and the copied decisions, and
tick the confirmation. The video is uploaded as **private** and unscheduled. Publishing is
done by you in YouTube Studio. Details: [SHARED_REVIEW_PACKAGE.md](SHARED_REVIEW_PACKAGE.md).

## Other workflows

Workflows prefixed with **⚙ Internal** are specialist steps, diagnostics, provider checks,
experimental renderers, or parts of multi-step handoffs. None of them holds YouTube
credentials. `manual-youtube-private-upload.yml` is retired and uploads nothing.
