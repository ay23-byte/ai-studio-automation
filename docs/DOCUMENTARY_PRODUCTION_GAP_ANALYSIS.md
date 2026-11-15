# Documentary production: audit, gaps and evidence

Scope: the long-form documentary workflow (`.github/workflows/make_longform.yml`,
`python run.py long …`, `core/pipeline.py::build_longform`). Every statement below is tied to a
workflow run, a test, or a file. Where something was not verified, it says so.

## 1. How the evidence was gathered

| Source | What it shows |
| --- | --- |
| Annotations of 12 earlier `make_longform.yml` runs (11–14 Nov) | Why each run failed |
| Eight production test builds from pull request #218 (real providers, real runner) | Runs 43647143241, 43652866814, 43655186698, 43681187522, 43691314957, 43702915253, 43716476390, 43717461589 |
| `offline-presenter-render` job on GitHub-hosted runners (female and male) | Presenter figures at 1920x1080 |
| Test suite | 932 tests before this work; 1084 after (merged with `main` at `8c07fa9`), all passing |

Limits of the audit: workflow artifacts and raw logs could not be downloaded from the audit
environment, so nobody has watched the produced videos as part of this work. Measured figures
come from the reports each build writes; picture and sound quality still need a person.

## 2. State before this work

Last successful documentary build: run 43029740217 on 11 Nov. Every run on 13 and 14 Nov failed
or was cancelled:

| Runs | Failure |
| --- | --- |
| 43641966044, 43430301113, 43425560985 | Presenter recorder `generate-clips.mjs` exited 1; its error text was never captured |
| 43682732606, 43692192233 (15 Nov, `main` at `f0875f2` and `b87a0c3`, after #219) | The same recorder still exits with code 1; no film |
| 43634417423 | Presenter clip recorded at 0.57 frames per second |
| 43627727865 | 11 of 18 visuals were fallback cards (limit 35%) |
| 43605766249, 43588472344, 43572252858, 43484926451, 43404962363 | Review-package step failed; the report said only "No failure detail was recorded" |

## 3. Gaps, causes and fixes

### 3.1 Narration lost about two thirds of its words (critical)

- **Problem.** Test build 43647143241 finished green with a 6 min 17 s video for an 18 minute
  request. Subtitles showed each full phrase; the voice spoke only its beginning.
- **Root cause.** `core/voice.py::_to_wav` trimmed silence with FFmpeg
  `silenceremove=…:stop_periods=1:stop_duration=0.12`. A positive `stop_periods` does not trim the
  end of the audio: it stops output at the first 0.12 s pause. Each phrase of up to 220 characters
  was cut at its first comma or full stop. Introduced by #213 on 14 Nov. Reproduced locally: a
  5.05 s test phrase came out as 1.56 s.
- **Already present.** A word-count gate on the script (`validate_longform_runtime`).
- **Missing.** Any check that the synthesised audio contains the text, or that the finished
  narration reaches the requested runtime.
- **Fix.** `trim_edge_silence` trims only the two ends, on the samples. `check_narration_complete`
  refuses narration that is too short for its text (more than 26 characters a second overall, or
  39 for a single phrase). `measured_runtime_report` compares the measured narration with the
  request before any visual is fetched and writes `runtime_report.json`.
- **Files.** `core/voice.py`, `core/pipeline.py`. **Tests.** `tests/test_narration_completeness.py`.
- **Acceptance.** Build 43652866814: 2903 words spoken in 19.86 min (146 words a minute), 110% of
  the 18 minute request.

### 3.2 Presenter clips: 0.57 fps, crashes, and a false "freeze"

- **Problem.** The 3D presenter was recorded in real time with MediaRecorder on software WebGL
  and loaded its engine and models from a CDN during the build.
- **Fix.** `avatar_studio/offline.html` + `render-offline.mjs` render every frame against a
  virtual clock and pipe the frames to FFmpeg, so the frame rate does not depend on machine speed.
  Packages are pinned in `avatar_studio/package-lock.json`; nothing is fetched at render time.
  `core/presenter_quality.py::analyse_presenter_motion` counts different frames, freezes, mouth
  movement while speech is audible, and compares the picture with the renderer's own mouth track.
  A failed clip stops the build; a still portrait is never substituted.
- **Second cause found in production.** Build 43652866814 rejected chapter 7 for a 0.53 s freeze
  that was the presenter pausing between sentences. A frame now counts as new when it differs
  from the last new picture, so slow movement accumulates and a repeated picture does not.
- **Files.** `avatar_studio/`, `core/avatar_clip_generation.py`, `core/presenter_quality.py`,
  `core/pipeline.py`, `scripts/check_presenter_clips.py`. **Tests.** `tests/test_presenter_motion.py`.
- **Acceptance.** GitHub-hosted runner, 1920x1080, 30 fps: 239 different frames of 240, longest
  freeze 0.033 s, mouth moving in 98–99% of voiced frames, offset 0 frames, 122–162 s to render an
  8 s clip. In build 43647143241 all 7 clips passed.
- **Not verified.** Whether the lip-sync looks right to a viewer; the avatar is stylised, not
  photorealistic, and its mouth model is trained on English.

### 3.3 Failures without a reason

- **Root cause.** `core/run_report.py::collect` did not read the review packager's own list of
  blocking problems.
- **Fix.** Those problems become the failure reason; `core/failure_category.py` names the kind of
  failure (configuration, authentication, provider quota, network, research, asset shortage,
  presenter, subtitles, audio, licensing, editorial rejection, rendering) and the next action.
- **Tests.** `tests/test_failure_category.py`. **Acceptance.** Build 43652866814 reported
  `[Presenter] PresenterRenderError: 3D presenter clip for chapter 7 was rejected: …`.

### 3.3a A script that could not be written left no reason

- **Problem.** Build 43716476390 ended after three minutes with "The run stopped before a job
  was created; see the failing step." The script is written before a job exists, so the
  failure report had nothing to quote.
- **Fix.** `run.py` sends the cause as an error notice before the run ends; the run report
  and the run page quote it. Applies to documentaries and Shorts.
- **Files.** `run.py`, `core/run_report.py`. **Tests.** `tests/test_script_failure_alert.py`.

### 3.4 Evidence could only be read by downloading the artifact

- **Fix.** `scripts/annotate_build_evidence.py` prints the measured facts on the run page: final
  file and runtime against the request, script model and voice, narration words and rate, shot
  kinds and sources, every footage source's answers, scenes left without footage and what was
  searched, presenter figures, subtitle check, audio levels, research counts.
- **Tests.** `tests/test_build_evidence.py`.

### 3.5 Subtitles

- **Decision implemented.** Documentary subtitles are English only. Another target language is
  refused; subtitle text containing Devanagari or Hindi in Latin letters stops the build before
  rendering. Narration may stay Hindi or Hinglish.
- **Files.** `core/subtitle_translation.py`, `core/pipeline.py`. **Tests.**
  `tests/test_english_only_subtitles.py`. **Acceptance.** Builds 1 and 2: English check PASS on
  105 and 110 lines.

### 3.6 Runtime range

- `--minutes` accepts 10–30; the workflow offers 18, 20, 22, 25, 28, 30. The shell check in the
  workflow still rejected 28 and 30; fixed.

### 3.7 Footage reach

- **Problem.** In build 43647143241 only 8 of 19 searched scenes got footage; NASA was asked in
  10 of 61 searches of a James Webb documentary.
- **Root cause.** NASA was added to a scene's sources only when the scene wording contained a
  listed space word ("galaxy" but not "galaxies", no "Webb"). NASA's search also matches every
  word, so long queries returned nothing.
- **Fix.** The documentary's subject counts for every scene, the word list is wider, and a long
  query with no results is searched once more by its first subject words. Results are still
  ranked and accepted against the full query.
- **Files.** `core/footage_sources.py`. **Tests.** `tests/test_footage_nasa_rights.py`.
- **Still open** (see section 5): Library of Congress rate limit, DVIDS, NARA, Europeana results.

### 3.8 One picture per 30–40 seconds

- **Problem.** The script asks for 4–5 visual beats per chapter whatever the runtime. In an
  18 minute documentary one clip would loop for 30–40 s.
- **Fix.** `split_long_beat`: a beat longer than `LONGFORM_MAX_SHOT_SECONDS` (12) is covered by up
  to four different assets found for the same narration; when no further asset exists the last
  one stays, so the timeline has no gap. Fallback cards are not multiplied.
- **Files.** `core/pipeline.py`. **Tests.** `tests/test_narration_completeness.py`.

### 3.9 Half of the scenes searched for "documentary presenter studio"

- **Problem.** In build 43655186698, 20 of 38 searched scenes got no footage, and each of them
  had searched `documentary presenter studio` / `cinematic presenter close up`. The film had
  45 shots in 20 min 30 s, and ten of them were stock or generated pictures typed "presenter".
- **Root cause.** The model returned fewer than three visual beats for some chapters. The
  anchor repair then padded them with neutral "presenter" placeholder beats, and the build
  treated those as ordinary scenes to find footage for.
- **Fix.** `complete_longform_storyboard` runs after the script passes research and runtime.
  Each chapter needs one beat per 60 spoken words (3 to 10; `LONGFORM_WORDS_PER_VISUAL_BEAT`).
  A chapter with fewer real beats gets its own storyboard from the model, with short English
  archive searches and no presenter, host or interview. If that call fails, beats are built
  from the narration: the documentary's subject plus the names spoken in that passage.
  Placeholder beats are removed; chart, timeline, map, diagram and document beats are kept.
  A failure here never discards a script that already passed.
- **Follow-up after build 43691314957.** The added beats could be typed `diagram` or `map`,
  but they are filled with footage like any other scene: 29 of 100 shots were real clips
  labelled "diagram". Added beats are now typed only as the footage they will be
  (`stock`, `archival`, `location`, `person`).
- **Files.** `core/script_engine.py`. **Tests.** `tests/test_storyboard_completion.py`.
- **Acceptance.** The "Script and narration" notice shows the beats per chapter and how many
  placeholder beats were removed; no scene searches for a presenter.

### 3.10 Technical QA refused the finished film without saying why

- **Problem.** Builds 43655186698 and 43681187522 rendered the whole film and the review
  package then refused it. The first said only `qa/technical.json is FAIL`; the failing check
  was in a file inside the artifact.
- **Root cause of the missing reason.** The release contract reported the status of the QA
  file and not its content.
- **Fix.** The failing checks are stored on their own in `qa/technical.json` (`failures`) and
  are quoted in the blocking message, the run report and the annotation. The run page shows the
  picture audit result and the failing shots. Such a failure is now categorised "Technical QA"
  instead of "Editorial rejection".
- **Cause, confirmed by build 43681187522** (the first build that printed it): eight chosen
  clips were "severely underexposed", which means almost black. The picture audit runs after
  everything else and cannot be overridden, so one black clip cost a two-hour build.
- **Fix for the cause.**
  1. One rule, `picture_defects`, decides what an unusable picture is. The footage engine
     applies it to each downloaded clip and tries the next candidate when it fails.
  2. Before the edit, `repair_unusable_pictures` measures every B-roll shot with the same rule
     on the seconds the shot shows. A failing shot gets other footage, then an explainer card.
     If nothing can replace it, the build stops there with the shot and the reason, before the
     render.
  3. Sample frames are taken from the part of the clip that is shown. They used to come from
     the middle of the source file, which could be minutes away from the picture in the film.
  4. A dark picture whose brightest 1% reaches level 60 of 255 (a night sky, a deep field) is
     marked for review; a black or uniformly dim frame still fails.
  5. The audit fails a source below 640x360, while the footage engine accepted clips down to
     320x240 and still images down to 600 wide. Long-form builds now use the audit's floor.
- **Files.** `core/review_contract.py`, `core/review_packaging.py`, `core/failure_category.py`,
  `core/visual_consistency.py`, `core/visual_production.py`, `core/footage_engine.py`,
  `core/archival_images.py`, `core/openverse_images.py`, `core/pipeline.py`,
  `scripts/annotate_build_evidence.py`, `scripts/package_longform_review.py`.
- **Tests.** `tests/test_review_contract.py`, `tests/test_visual_consistency.py`,
  `tests/test_picture_repair.py`, `tests/test_footage_engine.py`,
  `tests/test_longform_review_package.py`, `tests/test_failure_category.py`,
  `tests/test_build_evidence.py`.

### 3.11 A male presenter was given a female voice

- **Problem.** Build 43691314957 asked for a male voice and was narrated by
  `hi-IN-SwaraNeural`, a female voice, next to the male 3D presenter. The build was green.
- **Root cause.** When one phrase failed three times, the whole narration was started again
  with the next voice in a list, and the list for a male narrator held a female voice.
- **Fix.** Fallback voices have the requested gender only. A failed phrase is tried again with
  the same voice after a wait (20, 40 and 80 s), keeping the phrases already spoken, before
  another voice is used. If no voice of that gender works, the narration moves to the next
  provider or fails; it does not change the speaker. The "Narration" notice names the voice
  and says when it is not the configured one.
- **Checked.** Build 43717461589 asked for a male voice; `hi-IN-MadhurNeural` failed and
  `en-IN-PrabhatNeural` (male) spoke.
- **Files.** `core/voice.py`, `scripts/annotate_build_evidence.py`.
  **Tests.** `tests/test_voice_gender_consistency.py`, `tests/test_build_evidence.py`.

### 3.12 Watching the film needed a 13 GB download

- **Problem.** The review artifact of builds 43681187522 and 43691314957 is 13 to 14 GB,
  because it holds the whole build directory with every source clip.
- **Fix.** Pull request #220 on `main` added a second artifact, `longform-mp4-<run>`, with
  the finished film only. This branch is merged with it and adds no artifact of its own.
  The review artifact is unchanged, because the release workflow reads it.
- **Still open.** The review artifact itself is not smaller (see section 5).
- **Files.** `.github/workflows/make_longform.yml`.

### 3.13 Production tests could not be started from a pull request

- **Fix.** `make_longform.yml` also runs for a pull request that changes
  `.github/documentary-production-request.json`, and only when the newest commit message contains
  `[documentary-run]`. Values are checked against the form's choices. The build uploads nothing.
- **Release of such a build.** This work first made these builds unreleasable. The owner
  changed that on `main` (#221): `scripts/verify_source_run.py` accepts a pull-request build
  when the pull request comes from this repository and targets the release branch. The review
  and release gates still apply.
- **Files.** `scripts/resolve_documentary_request.py`. **Tests.** `tests/test_documentary_request.py`.

### 3.14 Merged with `main` (pull requests #219 to #222)

- `main` gained its own exposure floor in `core/footage_validation.py` (a clip whose three
  sample frames average under 12 of 255 is refused), a higher scene-match floor (0.20, was
  0.12) and the `longform-mp4-<run>` artifact while this work was in progress.
- Both exposure rules now run: the validation floor first, then the picture rule of the visual
  audit (burnt-out, blank and undersized pictures). A clip refused as too dark is remembered,
  so a later scene does not download it again.
- #221 (release of same-repository pull-request builds) and #222 (compact review export) are
  merged in unchanged.
- **Files.** `core/footage_engine.py`. **Tests.** `tests/test_footage_engine.py`,
  `tests/test_documentary_request.py`.

### 3.15 A robot voice, and two hours spent on a film that could only be refused

- **Problem.** In build 43702915253 neither male Edge voice answered ("No audio was
  received"). The narration fell back to the offline robotic voice (espeak-ng). The build then
  searched footage and rendered for 2 h 10 min, and audio QA refused the film for that voice,
  as it always does. The message named the last voice only, so it does not show which phrase
  stopped the first voice or whether the service was down.
- **Root cause.** Not established. Edge TTS is a free, unofficial service; it failed in two of
  the last three builds (one phrase in build 43691314957, every voice in 43702915253).
- **Fix.**
  - A documentary stops at the narration when only the robotic voice is left. The failure
    takes minutes, not hours. Short formats keep the fallback.
    `LONGFORM_ALLOW_ROBOTIC_NARRATION=1` restores the old behaviour.
  - The message names every voice, the phrase that stopped it (number and first words) and
    the cause.
  - One voice is tried four times (waits 20, 40, 80 s). The last try sends the phrase without
    symbols, in case the service refuses a character.
  - Text with no letter or digit ("...", a stray dash) is never sent to the voice.
- **Checked.** Build 43717461589: the configured voice failed again, the second male voice
  spoke the whole narration, and the notice says so. The early stop itself did not occur in
  a production build; it is covered by tests only.
- **Still open.** If Edge TTS keeps failing, the documentary needs a paid or self-hosted
  voice. This needs an owner decision (see section 5).
- **Files.** `core/voice.py`, `core/pipeline.py`, `core/review_packaging.py`, `core/run_report.py`.
  **Tests.** `tests/test_robotic_narration_gate.py`, `tests/test_voice_gender_consistency.py`.

## 4. Production test builds

Topic: "How the James Webb Space Telescope Sees the First Galaxies", 18 minutes, Hinglish
narration (configured voice Edge `hi-IN-MadhurNeural`), male 3D presenter, script by `gemini-3.1-flash-lite`.

| Build | Result | Measured |
| --- | --- | --- |
| 43647143241 | Green, but wrong | 6 min 17 s video (35% of request); 7 of 7 presenter clips passed; 6 fallback cards of 21 visuals; 8 of 19 scenes got footage; audio −15.9 LUFS, true peak −4.1 dBTP |
| 43652866814 | Failed in 20 min | Narration 19.86 min, 2903 words; presenter chapter 7 rejected for a pause (false positive) |
| 43655186698 | Film rendered, review package refused | 20 min 30 s video (114% of request), 1920x1080, 228 MB; narration 2913 words in 19.92 min; 7 of 7 presenter clips passed (lowest 28.2 different frames a second, longest freeze 0.03 s); 45 shots, 6 fallback cards (19%); 18 of 38 scenes got footage (Wikimedia Commons 7, Pixabay 5, DVIDS 4, NASA 2); audio PASS, −15.9 LUFS, range 2.6 LU; technical QA FAIL with no reason shown (3.10) |
| 43681187522 | Film rendered, review package refused | 19 min 26 s video (108% of request), 522 MB; narration 2456 words in 17.04 min; storyboard 44 beats in 7 chapters, 15 placeholder beats removed; 103 shots, no fallback cards; 89 of 89 searched scenes got footage (Wikimedia Commons 48, Pixabay 15, DVIDS 10, NASA 9, Internet Archive 7); 7 of 7 presenter clips passed; audio PASS, −16.0 LUFS; picture audit: 56 pass, 39 review, 8 fail (almost black clips), so technical QA FAIL, this time with the reason (3.10). Build step 112 min |
| 43691314957 | Green: film and review package | 19 min 25 s video (108% of request), 1920x1080, 585 MB; narration 2461 words in 16.76 min, **spoken by a female voice although male was requested (3.11)**; storyboard 44 beats in 7 chapters, 8 placeholder beats removed; 100 shots, no fallback cards; 85 of 85 searched scenes got footage (Wikimedia Commons 44, Pixabay 16, NASA 15, DVIDS 7, Internet Archive 3); picture audit: 58 pass, 42 for review, 0 fail; 7 of 7 presenter clips passed (lowest 29.1 different frames a second); English subtitles PASS on 92 lines; audio PASS, −16.0 LUFS, true peak −4.3 dBTP. Build step 116 min, review artifact 13.1 GB |
| 43702915253 | Film rendered, review package refused (robot voice) | Merged with `main`. Both male Edge voices failed, narration by espeak-ng: audio QA FAIL. 21 min 13 s video (118% of request), 605 MB; narration 2909 words in 18.64 min; storyboard 53 beats, 14 placeholder beats removed; 121 shots, 1 fallback card, 6 still images; 100 of 109 searched scenes got footage (Wikimedia Commons 48, Pixabay 15, DVIDS 14, NASA 12, Internet Archive 11); 9 scenes without footage, mostly "not relevant enough" and the 0.20 scene-match floor from `main`; picture audit 66 pass, 55 review, 0 fail; 7 of 7 presenter clips passed; English subtitles PASS on 111 lines; build step 130 min |
| 43716476390 | Failed in 3 min, before a job existed | The script could not be written; the cause was not recorded (3.3a) |
| 43717461589 | Green: film and review package | Final state of this work, merged with `main` at `8c07fa9`. 20 min 57 s video (116% of request), 1920x1080, 696 MB; narration 2732 words in 18.65 min by `en-IN-PrabhatNeural`, a male voice, because `hi-IN-MadhurNeural` did not answer; storyboard 47 beats in 7 chapters, 15 placeholder beats removed; 104 shots, no fallback cards, 1 still image; 89 of 91 searched scenes got footage (Wikimedia Commons 39, Pixabay 18, NASA 14, DVIDS 12, Internet Archive 6); picture audit 53 pass, 51 review, 0 fail; 7 of 7 presenter clips passed (lowest 28.8 different frames a second); audio −16.0 LUFS, true peak −4.3 dBTP, range 1.8 LU (REVIEW); English subtitles PASS on 100 lines; research 8 sources, 15 claims, all linked; build step 103 min; artifacts: review 12.5 GB, film 694 MB, diagnostics 188 KB |

## 5. Open items

| Item | Status |
| --- | --- |
| Human review of a finished documentary (picture, lip-sync, voice, subtitles) | Not done by anyone yet |
| Library of Congress | HTTP 429 with a 1200 s wait in builds 1, 4 and 5, read timeouts in build 3; almost every result is refused for having no publication date before the public-domain cutoff. No clip used in any build |
| DVIDS | Works since build 3; 7 clips used in build 5 |
| NASA | Asked for every scene since build 3; 15 clips used in build 5. 16 results refused because the item text names a third party |
| NARA | Every result is marked "Restricted - Possibly" and refused by the rights rule (261 in build 5); needs an owner decision |
| Internet Archive | 817 results and 3 clips used in build 5 |
| Europeana | 351 searches, 2 results, none used in build 5; cause not investigated |
| Narration provider | The configured voice `hi-IN-MadhurNeural` did not complete the narration in builds 43691314957, 43702915253 and 43717461589. The last one was spoken by `en-IN-PrabhatNeural`, an Indian English male voice reading Hinglish; a person has to judge how that sounds. The cause is in `narration_synthesis_report.json` (`voice_failures`) of the `longform-qa-diagnostics-43717461589` artifact and, from the next build on, in the "Narration" notice. Edge TTS is free and unofficial; a paid or self-hosted voice needs an owner decision |
| Runtime above the request | Builds 43702915253 and 43717461589 were 21 min 13 s and 20 min 57 s for an 18 minute request (118% and 116%). There is a lower limit (80%) and no upper limit |
| Audio loudness range | 1.7 LU in build 1 (REVIEW); 2.6 LU in build 3 (PASS) |
| Script model | The repository variable selects `gemini-3.1-flash-lite`; a stronger model may write better scripts. Not tested |
| News Shorts presenter | Still uses the old real-time recorder (`core/news_short_avatar.py`); outside this scope |
| AI video | Disabled in the workflow by policy |
| Review artifact size | 12.5 to 14 GB for an 18 minute film: every source clip is stored in full and again inside the review package. Not reduced, because the release workflow depends on its layout |
| Build time | About two hours for 18 minutes (80 minutes before the storyboard had more beats). Each provider is searched about 350 times; where the time goes was not measured |
| Picture audit "review" shots | 51 of 104 shots in build 43717461589 (42 of 100 in 43691314957) are marked soft, low in contrast or unlike the rest. A person has to look at them |
| Coverr | Builds 1 and 3: its 4 results were AI-generated library content and were refused. Since build 43681187522 the key is refused with HTTP 403; needs the owner's key |

## 6. How to run a production test

1. Edit `.github/documentary-production-request.json` on a pull request branch.
2. Push a commit whose message contains `[documentary-run]`.
3. Read the notices titled "Build evidence" on the run page. Download `longform-mp4-<run>`
   to watch the film, or `longform-review-<run>` for the full review package.

To build for release, use **Run workflow** on "2. Build a Documentary — review required".
