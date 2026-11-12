# Production completion ledger

One entry per completed task: root cause, files, pull request, tests, CI result, end-to-end
result and remaining limits. Statuses use the vocabulary in
`docs/PRODUCTION_READINESS_AUDIT.md`. An entry is never marked beyond what was demonstrated.

## Phase 0 — groundwork (PR #198, merged as `57d7081`)

| Task | Root cause | Status |
|---|---|---|
| Long-form chapter drift | Slate budget ignored the presenter intro | CI VERIFIED (unit tests); offline render showed 0.0 s drift |
| Audio clipping check never fired | Pattern never matched FFmpeg `astats` output | CI VERIFIED |
| Credit slate counted as black/silent video | QA measured the whole file instead of the body | CI VERIFIED |
| 22 tests never ran in CI | pytest-style tests were not collected by `unittest` | CI VERIFIED (`validate` runs pytest) |
| English narration always sent for translation | Missing `needs_translation` check | CI VERIFIED |
| Remakes of already-uploaded topics | Pre-ledger uploads were not consulted | CI VERIFIED |
| Single guarded publish path, duplicate ledger, state branch, health check | New capability | UNIT TESTED; **not verified on Actions or against YouTube** |

Validate run on `main`: 43312611000, both jobs green.

## Phase 1 — stabilization

Pull request: [#199](https://github.com/ay23-byte/ai-studio-automation/pull/199). Results are in
the table at the end of this section.

### 1.1 Daily news collection crashed on a missing voice package (brief 3A)

* **Problem:** scheduled run 43305710825 failed with `ModuleNotFoundError: No module named 'edge_tts'`.
* **Root cause:** `core/voice.py` imported the optional Edge TTS package at module level. The
  collect workflow installs only `requests` and `Pillow`, and its test glob picks up
  `tests/test_news_language_voice.py`, which needs `core.voice` only for voice *selection*.
  Reproduced locally in a virtualenv with those two packages (109 tests, 1 import error).
* **Fix:** the package is loaded when speech is synthesised. A missing package raises
  `VoiceProviderUnavailable` with the install command, is not retried per voice or per attempt,
  and the cascade moves on to the offline voice with the reason written to
  `narration_synthesis_report.json`. If no provider exists at all, narration stops with one
  actionable message. `run.py doctor` reports the narration provider. The workflow keeps its
  minimal install; no heavyweight package was added and no test was removed.
* **Also fixed:** the narration report named the configured voice even when a fallback voice
  had spoken; it now records the voice actually used.
* **Files:** `core/voice.py`, `core/health.py`, `.github/workflows/daily-news-shorts.yml`,
  `.github/workflows/validate.yml`, `tests/test_optional_voice_provider.py`.
* **Regression tests:** import and voice selection with the package blocked (child process);
  every `test_*news*.py` module imports with only the collect workflow's packages; no per-voice
  retry for a missing package; fallback reason recorded; no-provider failure; workflows that
  render narration install the provider. New CI job `news-collect-minimal-env` runs the news
  tests in the exact minimal environment on every pull request.
* **Rollback:** revert the commit; no data or schema change.
* **Status:** UNIT TESTED locally (135 news tests pass with only `requests` + `Pillow`).
  CI VERIFIED on the pull request (`news-collect-minimal-env` job). The scheduled workflow
  itself is re-run after merge.

### 1.2 Gemini reliability (brief 3B)

* **Problem:** three call sites answered the same questions differently.
* **Root causes:** two different hard-coded fallback lists (`core/config.py` and
  `core/news_shorts_writer.py`); an invalid key looked like any rejected request, so the news
  writer tried every model with the same dead key; the news writer used fixed 10/30/60 s waits
  without jitter; a non-JSON HTTP body escaped as a raw decoder error; nothing recorded which
  model served a request; the visual check turned a retired model id into an "error" for every
  shot instead of using the next model; an unset repository variable (empty string) could
  remove all fallbacks.
* **Fix:** `core/gemini.py` holds the policy once: the only default model ids, validation of
  ids before they reach a URL, failure classification (invalid key, unknown model, rate limit,
  server error, transient, bad request, network, malformed), bounded backoff with jitter under
  a hard cap, `Retry-After` in seconds or HTTP-date form, and a usage record
  (`output/logs/llm_usage.jsonl`, no prompts, no keys). All three call sites use it. Generated
  scripts carry a `generation` block naming the provider and model. An invalid key stops at
  once with the secret to fix. `run.py doctor` validates the configured chain.
* **Files:** `core/gemini.py` (new), `core/config.py`, `core/script_engine.py`,
  `core/news_shorts_writer.py`, `core/visual_semantics.py`, `core/llm.py`, `core/health.py`,
  `tests/test_gemini_policy.py`, `tests/test_news_gemini_fallback.py`,
  `tests/test_news_shorts_writer.py`.
* **Existing tests changed, and why:** `tests/test_news_shorts_writer.py` asserted exact sleeps
  of 10/30 s and the writer's private two-model default. Jitter is now part of the delay, so
  those tests pin jitter to zero and keep the same assertions; a new test asserts the jitter
  itself. The default-list tests now assert the shared chain and still assert order and
  de-duplication with explicit lists. No assertion was removed.
* **Limits:** all HTTP is mocked; **no real Gemini request was made**. Nine workflows still pin
  model ids in YAML. The news writer still has no non-Gemini fallback provider.
* **Rollback:** revert the commit. The `generation` key in scripts is additive.
* **Status:** UNIT TESTED.

### 1.3 News script draft failures gave no reason (issue #46)

* **Problem:** `/approve-script` failed three times on 2026-11-12 (runs 43220778290,
  43264312306, 43266538505) and the issue comment only said "check the logs".
* **Root cause of the failures:** UNKNOWN. The packet passes the source gate when replayed
  locally; the step ran 3–10 minutes, which points at Gemini availability or draft validation.
  Logs are not reachable from the audit sandbox.
* **Fix (diagnostics, not a behaviour change to any gate):**
  `scripts/draft_reviewed_news_short.py` classifies the failure (packet rejected, credentials,
  provider unavailable, validation failed, configuration), exits with a distinct code, writes a
  redacted `draft_failure.json`, a job summary and an error annotation; the workflow posts the
  category, detail, models tried and next step on the issue.
* **Files:** `scripts/draft_reviewed_news_short.py`, `.github/workflows/news-script-draft.yml`,
  `tests/test_news_gemini_fallback.py`.
* **Limits:** `issue_comment` workflows run from `main`, so this cannot be exercised from the
  pull request. It is verified only when you next comment `/approve-script` after the merge.
* **Status:** UNIT TESTED.

### 1.4 Database and concurrency (brief 3C)

* **Finding:** no defect found. The existing design (transactions, `busy_timeout`, backup before
  migration, JSON-only state branch) held up under new tests.
* **Added:** `tests/test_db_safety.py` (parallel writers, rollback, one ledger row per topic
  under concurrency, backup restore after corruption, backup pruning, idempotent re-init, Git
  ignore rules, no workflow commits a database or media file).
* **Open decision:** `db/studio.sqlite` is still tracked in Git (six historical rows). It was
  not removed because deleting your data needs your authorization.
* **Not verified:** the `automation-state` branch does not exist on GitHub yet.
* **Status:** UNIT TESTED.

### 1.5 Validate workflow (brief 3D)

* Run 43312611000 inspected: `unit-tests` and `ffmpeg-smoke` both succeeded.
* `validate.yml` gained per-job timeouts, a concurrency group (a newer push to a pull request
  cancels the older run; runs on `main` are never cancelled) and the minimal-environment job.

### Results

| Check | Result |
|---|---|
| Local `pytest tests -q` | 612 passed, 67 subtests (527 before this phase) |
| Local `python -m unittest discover -s tests` | 590 tests OK |
| Local news tests with only `requests` + `Pillow` | 135 tests OK (was: 109 tests, 1 import error) |
| Tracked database unchanged | sha256 prefix `6e3232feda4015cc` before and after |
| Pull-request checks on #199 (first commit `0c67ce8`) | All four passed: `unit-tests`, `ffmpeg-smoke`, `news-collect-minimal-env` (validate run 43318019701) and `unit-and-live-checks` (run 43318021641) |
| `validate` on `main` after merge | Happens after this file is merged; recorded in the Phase 2 entry |
| "Daily news Shorts — research queue" re-run on `main` | Happens after this file is merged; recorded in the Phase 2 entry |

### Carried forward

Job summaries on the remaining production workflows; repository-variable overrides for model ids
in nine workflows; the dead `REPLICATE_API_TOKEN` reference; Node 20 action deprecations; bare
`ffmpeg`/`ffprobe` calls in `core/voice.py`, `core/audio_quality.py`, `core/editor_longform.py`;
review of the seven open pull requests; everything marked NOT VERIFIED in the audit.

### Phase 1 follow-up (recorded in Phase 2)

| Check | Result |
|---|---|
| Pull-request checks on #199 (second commit `0333021`) | All four passed (validate run 43318628501, footage validation run 43318628933) |
| PR #199 | Merged into `main` as `0e2ba44` |
| `validate` on `main` after the merge | Run 43319210500: success, all three jobs (`unit-tests`, `ffmpeg-smoke`, `news-collect-minimal-env`) |
| "Daily news Shorts — research queue" re-run on `main` | **Still pending.** Workflows cannot be dispatched from the audit sandbox (HTTP 403) and no scheduled run had started by 23:07 UTC on 2026-11-12; the next schedule is 02:30 UTC. The fix is covered by the `news-collect-minimal-env` job, but the scheduled workflow itself is NOT VERIFIED |
| Issue #46 `/approve-script` re-run | Not re-run by the owner yet; cause of the three failures remains UNKNOWN |

## Phase 2 — reliability

Results are in the table at the end of this section.

### 2.1 Production runs gave no account of themselves

* **Problem.** A failed or skipped run of `make_shorts.yml`, `daily-news-auto-short.yml` or
  `make_longform.yml` showed a red or green mark and a log. `run_daily.py` exits 0 without
  building anything in three different situations, so "green" did not mean "a video exists".
* **Root cause.** The facts were already written to disk by the pipeline, but nothing collected
  them, and the failure alert was a fixed sentence without a reason.
* **Change.** `core/run_report.py` assembles one report per run from the job row and the
  per-job evidence files; `scripts/write_job_summary.py` writes the GitHub job summary,
  `output/run_report.json` and one run annotation, and always exits 0;
  `scripts/alert_on_failure.py` sends the outcome and reason to the notification channels.
  Outcomes: uploaded, built and upload blocked, built, skipped, failed.
  `core/publish.py` now records whether the stored privacy was actually read back from YouTube
  (`privacy_verified`) so the summary does not assume it.
  `scripts/upload_documentary_artifact.py` leaves `youtube-upload-failure.json` when it refuses
  or fails, so a blocked documentary upload has a stated reason.
* **Honesty rules in the report.** A job is only reported if its build directory exists in this
  run; missing evidence prints "not recorded"; a job marked done without its file is a failure;
  a notification older than the run (default window 3 hours, `RUN_REPORT_SINCE` to set it
  exactly) is never quoted as the reason. The last rule was added after a sandbox check quoted
  an unrelated earlier alert as the reason for a skipped run.
* **Known cosmetic issue.** `core/notify.py` also appends each notification to the job summary,
  so those lines appear above the report table.
* **Status.** UNIT TESTED (`tests/test_run_report.py`, 19 tests), demonstrated on two sandbox
  renders. **NOT VERIFIED on Actions**: none of the three workflows has run since.

### 2.2 Model ids were pinned in workflow YAML

* **Root cause.** Ten workflow files contained model ids (Phase 1 said nine literal plus two
  overridable; the correct split is below): eight as literals and two
  (`news-script-draft.yml`, `news-short-one-click.yml`) as the default of a `vars.` expression.
  Retiring a model meant editing up to ten files.
* **Change.** No workflow contains a model id. Workflows pass `vars.GEMINI_MODEL` /
  `vars.GEMINI_FALLBACK_MODELS` (or the `…_LONGFORM` pair); empty selects a built-in chain.
  The three documentary workflows used a different order (lite model first), so that order
  became the `longform` profile in `core/gemini.py`, selected with `GEMINI_PROFILE=longform`.
  **No workflow's effective model order changed.** The live-validation preflight now checks the
  configured chain instead of one literal id. `run.py doctor` names the source of the chain.
* **Status.** UNIT TESTED (profile tests; a test rejects `gemini-<digit>` in any workflow file).

### 2.3 Action versions, and a latent break in state persistence

* **Problem.** Node 20 actions were being force-run on Node 24 (annotation on run 43319210500).
* **Finding while upgrading.** `actions/checkout` v6 and later keep the credential in a file
  under `$RUNNER_TEMP`, referenced by `includeIf.gitdir`. `scripts/persist_state.py` copied
  `http.*.extraheader` from `.git/config` into its temporary clone, so after the upgrade the
  push to `automation-state` would have been unauthenticated. The same script cloned before
  applying any credential, and it treated *any* `ls-remote` failure as "the branch does not
  exist yet", which would silently start a run with an empty duplicate ledger.
* **Change.** `persist_state.py` takes the credential from `GITHUB_TOKEN` (passed by both
  workflows), or from either checkout layout, and hands it to git through `GIT_CONFIG_*`
  environment variables (never argv, never written to the clone); network commands are retried
  three times with a 180 s limit; an unreachable remote is an error; git output is scrubbed.
  Then the hosted workflows moved to `checkout@v7`, `setup-python@v7`, `upload-artifact@v7`,
  `download-artifact@v8`, `github-script@v9`, `setup-node@v7`, `cache@v6`. The self-hosted
  Windows workflow was left on the Node 20 majors.
* **Status.** Credential handling is UNIT TESTED against real git for both checkout layouts.
  The push itself is **NOT VERIFIED**: the `automation-state` branch still does not exist
  because no build workflow has run. `checkout`, `setup-python`, `setup-node` and
  `upload-artifact` are exercised by pull-request checks (see Results); `download-artifact`,
  `github-script` and `cache` are not.

### 2.4 A build that failed part-way was lost

* **Root cause.** Checkpoints make a job resumable, but a GitHub runner is discarded when the
  job ends, so `run.py resume` could never be used on Actions.
* **Change.** `scripts/build_with_recovery.py` wraps the build command in the two scheduled
  workflows. A job created by the run that failed for a possibly transient reason is resumed
  once from its checkpoint and then recorded like a normal build (ledger, topic queue, news
  "seen" cache). A failure before any job exists reruns the command once. Configuration,
  credential, content-gate, quality-gate and release-block failures are never retried. Bounded
  by `BUILD_RECOVERY_ATTEMPTS` (default 1).
* **Evidence.** Sandbox, real pipeline: the demo Short was built with the render stage forced
  to raise; the wrapper resumed job 1, the log shows `reusing completed stage 'voice'`, and the
  result is a 27.2 s 1080x1920 H.264/AAC file with exit code 0. Narration in the sandbox is a
  test tone and all four visuals are fallback cards (no provider is reachable there), so this
  demonstrates the recovery mechanics, not output quality. The four visuals were fetched again
  on the resume: that is the existing rule that a resumed job retries any fallback card. Reuse
  of real downloaded visuals on resume was not exercised in this run.
* **Status.** UNIT TESTED (`tests/test_build_recovery.py`, 15 tests) and demonstrated in the
  sandbox. NOT VERIFIED on Actions. Long-form jobs are not resumed automatically.

### 2.5 Smaller items carried from Phase 1

| Item | Result |
|---|---|
| Script engine said "temporarily unavailable" for unknown model ids and malformed replies | The final error now distinguishes: every model returned 404 (a configuration problem), no usable reply without any rate limit or outage, or a genuine temporary outage. UNIT TESTED |
| Quality score in the log differed from the report (92 vs 72) | The technical check printed its score before the fallback-visual deduction. The final figure is now printed too. UNIT TESTED |
| Bare `ffmpeg`/`ffprobe` in `core/voice.py`, `core/audio_quality.py`, `core/editor_longform.py` | Now honour `FFMPEG_BINARY` / `FFPROBE_BINARY` like the rest of the code |
| No tests for the health checks added in Phase 1 | Added (model configuration, profile warning, narration provider levels) |
| `news-story-review.yml` had no timeout or concurrency group | Added; a test requires a timeout on every job |
| Unused variable in `core/news_shorts_writer.py` | Removed |
| `.env.example` pinned model ids | Now empty with an explanation, so a copied `.env` follows the built-in chain |

### Results

| Check | Result |
|---|---|
| Local `pytest tests -q` | **683 passed**, 284 subtests (612 after Phase 1) |
| Local `python -m unittest discover -s tests` | Ran 661 tests, OK |
| Local news tests with only `requests` + `Pillow` | 135 tests OK |
| Tracked database unchanged | sha256 prefix `6e3232feda4015cc` before and after |
| All 30 workflow files parse as YAML | Yes (PyYAML) |
| Pull-request checks | PR_CHECKS_PENDING |

### Not done in Phase 2, and why

* **Correction to Phase 1: `REPLICATE_API_TOKEN` is not a dead reference.** Phase 1 reported
  that no Python code reads it. That was wrong: `core/images.py` has an optional paid
  text-to-video adapter that reads it. The adapter runs only when `FREE_ONLY_MODE` is off and
  `AI_VIDEO_ENABLED=1` and a model is configured; every workflow that sets `AI_VIDEO_ENABLED`
  sets it to `0`, and `news-short-one-click.yml` passes the token to a step that also sets
  `FREE_ONLY_MODE=1`. So it cannot incur cost as configured, and nothing was removed. The
  recommendation to delete the reference is withdrawn; the audit row is corrected.
* No workflow was dispatched: the sandbox cannot dispatch workflows. Everything that depends on
  a real run of `make_shorts.yml`, `daily-news-auto-short.yml` or `make_longform.yml` is
  NOT VERIFIED.
* The tracked `db/studio.sqlite` decision is still open; the file is unchanged.

### Carried forward

Documentary end-to-end proof and the open documentary pull requests (#86, #87, #28, #10) in
Phase 3; a fixed-packet news Short end-to-end test and #37 in Phase 5; a real private upload
with privacy read-back and #154 in Phase 6; dashboard tests and the operator documents in
Phase 7. Long-form automatic resume; job summaries on the manual review/upload workflows.
