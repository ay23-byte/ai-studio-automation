# Production readiness: status and remaining gaps

> Evidence-based status by feature, all workflows, open pull requests and risks:
> [`PRODUCTION_READINESS_AUDIT.md`](PRODUCTION_READINESS_AUDIT.md). Per-task root causes, tests
> and results: [`PRODUCTION_COMPLETION_LEDGER.md`](PRODUCTION_COMPLETION_LEDGER.md).

Status as of the audit branch. "Verified" means exercised by a real run or an
automated test in the audit environment. "Not verified" means the code path
exists and is unit-tested with mocks, but could not be run against the real
service from that environment.

## What was verified

| Area | Evidence |
| --- | --- |
| Shorts build end to end | Real FFmpeg render, 1080x1920 H.264/AAC `yuv420p` 30 fps, technical QA REVIEW 92/100 |
| Long-form build end to end | Real render of the built-in documentary script, 1920x1080, captions, chapter slates, credits |
| News Short end to end | Story selection, grounded script, render, release gate, from a recorded feed sample |
| Resume | Shorts: injected render failure, then resume without re-synthesising narration (automated test). Long-form: a render killed mid-way was resumed; narration and captions were reused |
| Long-form picture timing | Every chapter's shots add up to that chapter's narration (was drifting by the presenter intro length per chapter) |
| Devanagari captions | Rendered through libass with the bundled font |
| Duplicate prevention | Ledger, database and marker layers (automated tests) |
| Private-only upload | Request body, authorisation rules, read-back correction (automated tests with a mocked API) |
| Database migration | Legacy schema migrated with all rows intact and a backup written |
| Workflows | YAML parses; no user input interpolated into shell; no broken line continuations |

## What could not be verified here

The audit environment had no API keys and could not reach the media providers.

| Area | Why | How to verify |
| --- | --- | --- |
| Real YouTube upload, channel verification, read-back | No OAuth credentials | Run `make_shorts.yml` manually with `upload` ticked; confirm a private video on the channel |
| Gemini and fallback providers | No keys | `python run.py doctor`, then `python run.py shorts "..."` |
| Pollinations, Pixabay, Wikimedia, Internet Archive, Library of Congress | Hosts unreachable | Any build with network access; check `visual_fallback_report.json` is absent or small |
| Edge TTS voices | Host unreachable; offline `espeak-ng` was used | Any build with network access |
| Live RSS feeds | Hosts unreachable; a recorded sample was used | `python scripts/run_daily_news_short.py` |
| GitHub Actions runs | Workflows cannot be dispatched from the audit | Dispatch each workflow once |
| `automation-state` branch on GitHub | Tested against a local bare repository | First scheduled run creates the branch |

Because no provider answered, every visual in the audit renders was a fallback
card, and the release gate correctly refused to upload them.

## First-run checklist

1. Add the secrets and the `YT_CHANNEL_ID` variable listed in `docs/ARCHITECTURE.md`.
2. Run `python run.py doctor` locally or read the "Preflight health check" step.
3. Dispatch "1. Create and upload a Short" with a topic and `upload` ticked.
4. Confirm on YouTube Studio: correct channel, visibility Private, no schedule.
5. Confirm the `automation-state` branch exists and contains `state/publish_ledger.json`.
6. Dispatch "Daily news Short — automatic (private upload)" once and read `auto_selection.json` in the artifact.
7. Let the schedules run for a week with private uploads before publishing anything in bulk.

## Known limitations

- **Factual accuracy is not verified automatically.** Content QA checks form
  and risky patterns. News grounding checks numbers and attribution against
  headlines and summaries, not full articles. Review private uploads before
  making them public.
- **Cross-language duplicates** are not detected by title similarity.
- **A Short that is built but whose upload fails on a runner** is kept as a
  workflow artifact for 14 days and recorded as built, so the topic is not
  rebuilt. Release it with the "Release approved Short (Private)" workflow, or
  remove its ledger entry to rebuild.
- **Long-form is not scheduled.** `make_longform.yml` remains manual, and its
  ledger entries are not persisted to the state branch.
- **Hindi text in thumbnails and cards** needs `libraqm`; without it that text
  is omitted rather than drawn incorrectly.
- **Presenter portrait rights.** Presenter shots are listed as blocked in the
  rights register until a person confirms ownership of the portrait. This is the
  existing documentary rule and was left in place.
- **The built-in demo scripts** are Hinglish finance examples narrated with the
  English voice setting. They exist to exercise the pipeline without API keys
  and are not channel content.
- **Existing database rows** keep `privacy_status = unlisted` from the old
  default. Three near-duplicate "Compound Interest" Shorts exist (ids 3, 5, 6);
  one is uploaded. The duplicate check now blocks uploading the other two.
- **`requirements.txt`** uses minimum versions, not exact pins.
- **`core/news_shorts_writer.py`** still contains validators written for one
  specific story packet in the human-reviewed news workflow.
