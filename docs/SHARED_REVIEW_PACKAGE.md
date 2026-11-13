# Review package and private release

Every production mode (documentary, standard Short, daily-news Short) ends the same way:

```text
build workflow  ->  review package (artifact)  ->  human review in the dashboard  ->  release workflow  ->  PRIVATE upload
```

A build workflow never uploads and never receives YouTube credentials. A release
workflow never builds; it only uploads a package a person has approved item by item.

## Where the rules live

| File | Role |
|---|---|
| `core/review_contract.py` | The single authority: manifest v2, decisions v2, and every release rule (`evaluate_release`). |
| `core/review_dashboard.py` | Builds the self-contained `review_dashboard.html`. |
| `core/review_packaging.py` | Helpers shared by the three packagers. |
| `scripts/package_build_for_review.py` | Build-side step: finds the one build and packages it. |
| `scripts/package_longform_review.py`, `package_standard_short_review.py`, `package_daily_news_review.py` | Translate each pipeline's outputs into the shared package. They copy evidence; they never invent it. |
| `scripts/verify_source_run.py` | Confirms a build run may be released (this repository, a build workflow of that mode, successful, same branch). |
| `scripts/apply_review_decisions.py` | Records decisions in a package after checking them. Writes nothing on refusal. |
| `scripts/release_review_package.py` | Checks everything again, uploads PRIVATE, reads the privacy back, writes the release report. |
| `schemas/production-manifest-v2.schema.json`, `schemas/review-decisions-v2.schema.json` | Document the two file formats; tests check them against real packages. |

## Package layout

```text
shared_review_package/
  video.mp4                        the exact file that would be uploaded
  captions.ass
  production_manifest.json         schema v2: job id, video SHA-256, scenes, assets, sources, claims
  review_dashboard.html            open this in a browser
  review_decisions_template.json   everything "pending"
  review_checklist.json / .md
  package_summary.json             REVIEW_REQUIRED or FAIL, with reasons
  qa/technical.json semantic.json audio.json captions.json assets.json
  preview/frames/frame_*.jpg
  assets/                          the visual used by each scene
  evidence/                        licence register, credits, research ledger, source reports
```

The job id is `<mode>-run<workflow run id>-a<attempt>-j<pipeline job id>`, so every
build has a distinct identity and a decision file cannot be reused for another build.

## Package status

- `REVIEW_REQUIRED`: the package is complete and waits for a person. The build run is green.
- `FAIL`: automated QA failed. The package is kept for inspection but **cannot be approved**,
  and the build run is red. Typical causes: the video does not decode, the configured narrator
  was unavailable and a robotic fallback voice was used, most visuals are fallback cards,
  the documentary gates (visual consistency, licence register, research ledger) did not pass.

There is no automatic "approved" status. `technical` QA must be `PASS` and cannot be
overridden. The other four reports are at best `REVIEW`: an automated pass is evidence for
the reviewer, not the approval.

## Review

1. Open the build run, download its artifact, unzip it.
2. Open `shared_review_package/review_dashboard.html` (inside `build/job_<id>/`).
3. Watch the whole video. Decide every scene, every asset (open the source, confirm the
   licence), every claim and source, and each QA report. Nothing is pre-selected and there
   is no "approve all".
4. Enter your name, then **Copy for the release workflow** (or download `review_decisions.json`).

The dashboard works offline and stores progress in the browser. It was tested with an
automated DOM (jsdom); opening it from disk in each real browser has not been tested.

## Release

Run **Release approved documentary (Private)** or **Release approved Short (Private)**:

| Input | Value |
|---|---|
| `source_run_id` | The numeric id of the build run (in the run summary and the URL). Never "latest". |
| `video_sha256` | Shown at the top of the dashboard and in the build run summary. |
| `review_decisions` | The text copied from the dashboard. |
| `review_decisions_path` | Alternative for very large reviews: path of a committed decisions file. |
| `confirm_private_upload` | Tick it. It is never pre-ticked. |

The release is refused, and nothing is uploaded, when any of these is true:

- the source run is not a successful build of that mode in this repository on the same branch;
- the run's artifacts do not contain exactly one review package;
- the MP4's SHA-256 differs from the input, the manifest, or the decisions (any re-encode invalidates the approval);
- the decisions belong to another job, are older than the package, are incomplete, or reject anything;
- an asset has unknown origin, no licence evidence, or a search-result page as its source;
- a QA report is `FAIL` or missing;
- `PRIVACY` is not `private`, or a publish time is set;
- the YouTube channel cannot be verified as the configured channel.

After the upload the workflow reads the video back from YouTube and requires privacy
`private`, no scheduled publish time, and the expected channel. The description carries
`AI-STUDIO-REVIEWED-SHA256:<sha>`; a rerun finds that marker and reuses the existing
private upload instead of uploading twice.

Exit codes of `scripts/release_review_package.py`: `0` uploaded and verified private,
`2` blocked before upload, `3` uploaded but verification failed (check YouTube Studio),
`1` error. `release_report.json`, `release_report.md` and `youtube-upload-result.json`
are kept as an artifact for every outcome.

Making a video public is not part of any workflow. Do it in YouTube Studio.

## Run it locally

```bash
python scripts/package_build_for_review.py --mode shortform        # after a local build
python scripts/validate_review_package.py build/job_1/shared_review_package
python scripts/apply_review_decisions.py build/job_1/shared_review_package review_decisions.json
python -m pytest tests/test_review_contract.py tests/test_review_release_e2e.py -q
```

## Older packages

Packages with `schema_version: 1` can still be validated, but they name no job, no video
hash and no reviewer, so they can never be released. Rebuild the video to get a v2 package.
`manual-youtube-private-upload.yml` is retired and uploads nothing.

## What is not verified

The release workflows, the YouTube upload and the read-back have been tested with a fake
uploader only. They have not been run on GitHub Actions against the real channel.
