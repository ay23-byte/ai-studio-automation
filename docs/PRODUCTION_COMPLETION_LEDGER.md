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
