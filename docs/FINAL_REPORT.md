# Final report: audit, repair and production readiness

Repository: `ay23-byte/ai-studio-automation`. State described: `main` at `64a9c35`
(pull requests #198, #199, #200, #207 and #208 merged) plus the follow-up pull request that
adds this file. Date: 2026-11-13.

**Summary.** The code paths that decide whether a video may be uploaded are now one path
with one set of rules, on GitHub Actions and on a local machine, and the automated tests for
them pass. The system has **not** been proven in production: no real YouTube upload, no live
provider call and no run of the rewritten workflows on GitHub Actions has been observed.
Section 6 lists exactly what is unproven and section 8 how to close each gap.

## 1. What was audited

| Area | Size | How |
|------|------|-----|
| Pipeline code | `core/` 82 files, `run.py`, `run_daily.py`, `dashboard.py`, `pages/` (about 19,500 lines) | Reading of the build, provider, render, QA, review and upload paths; AST scans; pyflakes; import and test-reference scans |
| Scripts | 35 in `scripts/` | Each run by path with `--help` (the way a workflow runs it); same scans |
| Workflows | 32 in `.github/workflows` | YAML scan of every step for expression injection, secrets, permissions, timeouts, triggers and action versions; reading of the build and release workflows |
| Tests | 106 files | Run in full under pytest and unittest |
| Configuration | 3 requirements files, 2 environment templates, `.gitignore`, 3 schemas | Compared with what the code reads |
| Data | `db/studio.sqlite` | Inspected from a read-only copy; never modified |
| Repository state | 205 branches, 7 open pull requests | `git ls-remote`, GitHub compare API, `gh pr view` |
| Documents | 24 in `docs/`, README | Checked against the code where they describe upload or review |

Not audited line by line: the optional avatar, SadTalker, OpenVINO, Kaggle and MuAPI paths
(scanned only), `avatar_studio/` JavaScript, and document text unrelated to review or upload.

## 2. Findings by severity

Numbers refer to `docs/PRODUCTION_READINESS_AUDIT.md` sections 14.1 (1 to 16) and 15.1
(17 to 37).

### High: 10, all fixed

| # | Finding | Fixed in |
|---|---------|----------|
| 1, 2 | The documentary build failed with `ModuleNotFoundError: No module named 'scripts'` (run 43484926451); 15 scripts had the same defect | #208 |
| 3 | Shorts and news builds produced no review package, so they could not be released at all | #208 |
| 4 | Review decisions named no job, no video hash and no reviewer; one file fitted any package | #208 |
| 5 | Documentaries had a separate upload path with a single tick box | #208 (retired) |
| 6 | A release could use the artifact of a failed or unrelated run | #208 |
| 7 | Privacy was requested as private but not read back after upload | #208 |
| 17 | Local uploads (`python run.py upload`, the local dashboard) skipped the human review. Listed as finding 12 (Medium) in #208; re-rated because a safety gate could be bypassed | Follow-up |
| 18 | No time limit on any FFmpeg render or mix | Follow-up |
| 19 | No time limit on 37 other `subprocess` calls | Follow-up |

### Medium: 12, 9 fixed, 3 open

| # | Finding | Status |
|---|---------|--------|
| 8 | Duplicate-upload lookup used YouTube search, which lags behind new uploads | Fixed in #208 |
| 9 | Robotic fallback narration and card-slideshow builds were packaged as reviewable | Fixed in #208 |
| 10 | The three build workflows shared one concurrency group | Fixed in #208 |
| 11 | YouTube credentials were passed to a build step that never uploads | Fixed in #208 |
| 20 | Downloads without total time limit, size limit or length check | Fixed |
| 21 | Edge TTS request without a deadline | Fixed |
| 22 | `FFMPEG_BINARY` / `FFPROBE_BINARY` ignored by 32 command lists | Fixed |
| 23 | `run_daily.py --upload` recorded a blocked upload as a failed topic | Fixed |
| 24 | Legacy news workflows dropped the artifact when packaging failed | Fixed, not run on Actions |
| 28 | No dependency lock file | Open |
| 29 | Actions pinned by tag, not commit | Open |
| 30 | `build_longform` is one 588-line function | Open |

### Low: 14, 7 fixed, 1 partly fixed, 6 open

Fixed: 13 to 16 (#208), 25, 26, 27. Partly fixed: 33 (undocumented environment variables).
Open: 31 (Streamlit deprecation), 32 (unreachable modules), 34 (modules without direct
tests), 35 (unused imports, silent `except` blocks), 36 (tracked database), 37 (branches and
old pull requests).

## 3. Fixes

**One review contract and one release path (#208).** `core/review_contract.py` is the only
place that decides whether a video may be released. Each build ends with a package
(manifest v2, dashboard, decision template) that names the job, the run and the SHA-256 of
the MP4. A release needs complete human decisions for that exact file. Only
`release_shorts_private.yml` and `release_documentary_private.yml` hold YouTube
credentials. Every upload is private.

**Local uploads under the same contract (follow-up).** The release gate that every local
upload passes through (`core/release_gate.py`) has a `human_review` check, implemented in
`core/local_review.py` with the same `evaluate_release` call as the workflows.

- `python run.py review --id N` builds the package and prints the dashboard path.
- `python run.py upload --id N --decisions FILE` records the exported decisions and uploads.
- The local dashboard has the same two steps under "Human review (required before upload)".
- `--upload` after a build (`run.py shorts`, `run.py long`, `run_daily.py`) prepares the
  review package; it no longer uploads, because a new video has no review.
- The upload description carries `AI-STUDIO-REVIEWED-SHA256:<hash>`, as in the workflows.

**Render reliability (follow-up).** `ffmpeg_utils.run` stops a command after
`FFMPEG_TIMEOUT_SECONDS` (default 1 hour) and raises `FFmpegTimeout`. All 37 other
`subprocess` calls have a limit (probes 2 minutes, external generators
`EXTERNAL_TOOL_TIMEOUT_SECONDS`, default 2 hours). The binary overrides reach every command.

**Provider reliability (follow-up).** `core/downloads.fetch` is used by all four asset
providers: total time limit (`MAX_DOWNLOAD_SECONDS`, 600), size limit
(`MAX_VIDEO_DOWNLOAD_MB` 600, `MAX_IMAGE_DOWNLOAD_MB` 80), `Content-Length` check, and a
`.part` file renamed only when complete. An Edge TTS request is abandoned after
`EDGE_TTS_TIMEOUT_SECONDS` (90) and retried. The LLM fallback keeps its retry when it first
has to switch JSON mode or model. Script providers and the YouTube client already had
timeouts and bounded retries; they were checked and left unchanged.

## 4. Tests

| Suite | Result |
|-------|--------|
| `python -m pytest tests` | **843 passed**, 446 subtests (808 on `main` before this pass) |
| `python -m unittest discover -s tests` | **821 OK** (786 before this pass) |
| New: `tests/test_local_review.py` | 16 tests: missing package, missing decisions, untouched template, one pending item, re-rendered video, rebuilt package, another job's decisions, CLI flow, `--upload` flags, real FFmpeg video |
| New: `tests/test_runtime_limits.py` | 16 tests: static scans (subprocess limits, HTTP timeouts, binary overrides), stalled command, download limits, Edge TTS stall, retry accounting |
| Changed: `tests/test_publish_safety.py`, `tests/test_e2e_shorts_render.py` | Unreviewed jobs never reach the uploader, also in `--dry-run`; a fresh build is blocked only by `human_review` |

CI (`validate.yml`): `unit-tests` runs the whole suite and checks that `db/studio.sqlite` is
unchanged; `news-collect-minimal-env` runs the news suite without optional packages;
`ffmpeg-smoke` renders real videos and runs the review-and-release path, now including
`tests/test_local_review.py`.

## 5. Evidence

Observed in the audit sandbox, with real FFmpeg and a fake uploader:

- Package → dashboard → decisions → private release passes for documentary, Short and news
  Short, with the packaging step run by path exactly as a workflow runs it.
- A local job: `upload` refused ("human_review: no review package exists"); `review` wrote
  the package; the untouched template was refused; exported decisions were accepted; one
  private upload call was made with the reviewed-file marker; re-rendering the video
  cancelled the approval.
- The local dashboard, driven through Streamlit's `AppTest` harness with a real rendered
  video: upload click before review → blocked with the reason; "Build review package" →
  package written; decisions uploaded and recorded → status "Reviewed"; upload click → one
  call with `privacy_status="private"`.
- A command that never finishes is killed at the limit and reported as `FFmpegTimeout`.
- `db/studio.sqlite` has the same SHA-256 before and after every test run.

## 6. What is not verified

1. **No real YouTube upload or read-back.** Every release ran against a fake uploader.
2. **The rewritten build and release workflows have not run on GitHub Actions.** Workflows
   cannot be dispatched from the audit sandbox. This includes `actions/download-artifact@v8`
   with `run-id` and the three legacy workflows edited in this pass.
3. **No live provider call.** Gemini and the fallback script providers, Edge TTS and the
   footage and image hosts were unreachable from the sandbox.
4. **No pipeline-built video has gone through approval.** The approve-and-release tests use
   constructed build outputs with a real rendered video, because a full build needs the
   providers in point 3.
5. **No real browser.** The review dashboard was tested in jsdom; the local dashboard in
   Streamlit's `AppTest` harness.
6. **The time limits were tested with a stand-in command**, not a stalled FFmpeg, and the
   download limits against fake responses.
7. **The default limits are estimates.** A long documentary mix on a slow runner could
   exceed one hour; the limit is configurable and the error says so.

## 7. Open risks

| Risk | Consequence | Mitigation in place |
|------|-------------|---------------------|
| First real workflow run fails on something a sandbox cannot show (artifact download, permissions, runner image) | A build or release run is red; nothing is uploaded | Release refuses unless every check passes; uploads are private |
| Behaviour change for local use | `--upload` no longer uploads; `python run.py upload` is refused until reviewed | The refusal names the two commands to run |
| Videos built before #208 | They have no v2 package and cannot be released | Rebuild; locally, `python run.py review` builds a package for an existing job if its build directory is intact |
| Unpinned dependencies and action tags (28, 29) | A future release of a dependency can break a build | None; recommendation in section 8 |
| `build_longform` size (30) | Regressions in the documentary path are hard to isolate | End-to-end and packaging tests |
| A reviewer approves without watching | The contract checks completeness and identity, not attention | The reviewer's name is recorded in the release report |

## 8. Next steps

In order:

1. Run "Create a Short" on Actions. Open the dashboard from the artifact, export decisions,
   run "Release approved Short (Private)". Confirm the video is private in YouTube Studio.
   This closes points 1, 2, 3 and 4 of section 6 for Shorts.
2. Do the same for one documentary and one news Short.
3. Open `review_dashboard.html` in Chrome or Edge from disk and confirm the export works.
4. Run one local cycle: `python run.py review --id N`, then
   `python run.py upload --id N --decisions review_decisions.json`.
5. Add a lock file (`pip-compile` or `uv pip compile`) and pin actions to commit hashes.
6. Split `build_longform` into stage functions with their own tests.
7. Decide the housekeeping items below.

## 9. Housekeeping recommendations (nothing was changed)

**`db/studio.sqlite`.** It is tracked although `.gitignore` excludes it. It holds 6 job rows
from GitHub runners; 4 are recorded uploads, and the upload path reads those rows to refuse
a remake of the same subject. Recommendation: confirm that the duplicate ledger on the
`automation-state` branch lists those 4 uploads, then stop tracking the file
(`git rm --cached db/studio.sqlite`) and keep the local copy. Until then, leave it.

**Branches.** 205 remote branches. 165 have no commit that is missing from `main` and can be
deleted. 38 have unmerged commits and are between 67 and 1,124 commits behind `main`; delete
them after the pull-request decisions below, unless one holds work still wanted.
`automation-state` holds the duplicate ledger and news cache: **never delete it**.

**Open pull requests.**

| PR | Title | State | Recommendation |
|----|-------|-------|----------------|
| #154 | Automatically upload completed documentaries to YouTube as Private | Conflicting | Close. It uploads straight after the render, which the review contract now forbids |
| #87 | Make long-form Gemini script generation more resilient | Conflicting | Close. `main` has model discovery, bounded retries and fallback providers; check the diff for any prompt change still wanted |
| #86 | Fix disappearing-villages visual beat regression | Mergeable into `production-trial/disappearing-villages` | Close with #28; its base is the trial branch, not `main` |
| #37 | Add ranked news clusters and evidence-gated Shorts drafts | Conflicting | Close. `core/news_auto.py` covers corroboration and gating on `main` |
| #28 | ci: trigger first documentary production trial | Conflicting, 17 files | Close. A trial trigger, 866 commits behind |
| #10 | Add more free real documentary footage sources | Conflicting | Close. `core/footage_sources.py` on `main` is multi-source; list any source in the diff that is still missing as a new issue |
| #2 | Add optional Intel OpenVINO presenter backend | Conflicting, 1,124 commits behind | Close, or redo on current `main` if the Intel presenter is still wanted |

**Legacy files without a decision.** `scripts/upload_documentary_artifact.py`,
`scripts/upload_approved_news_short.py`, `standard-shorts-review.yml` and the retired stub
`manual-youtube-private-upload.yml` are no longer part of any release path. Recommendation:
delete them once step 1 and step 2 above have succeeded.
