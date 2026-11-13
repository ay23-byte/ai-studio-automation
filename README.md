# AI Studio - YouTube Video Creator

Automated Hindi/Hinglish YouTube video creator for **Shorts (9:16)** and **long-form videos (16:9)**. It generates scripts with Gemini, creates visuals, synthesizes narration with Chatterbox (with Edge TTS fallback when selected), renders videos with FFmpeg, and can upload them to YouTube.

## Operations quick reference

Architecture, safety rules and recovery steps: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Current status and what still needs a live check: [`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md).

```bash
python run.py doctor                      # check tools, fonts, database, providers, credentials
python run.py status --id 12              # stage-by-stage progress of a job
python run.py resume --id 12              # continue a failed job from its checkpoints
python run.py review --id 12              # build the human-review package and dashboard
python run.py upload --id 12 --dry-run    # run the release gate (including the review) without uploading
python scripts/run_daily_news_short.py    # autonomous, source-gated news Short
```

Uploads are always **private** and unscheduled, go only to the configured channel
(`YT_CHANNEL_ID` / `YT_CHANNEL_HANDLE`), and are refused for duplicate subjects or
failed quality checks. Making a video public is a confirmed action in the dashboard.

## Features

- Create Shorts and long-form videos from a topic.
- Gemini script generation retries temporary service/network errors and rotates through the configured fallback models; permanent API-key or model-access errors stop early with a clear message.
- Automatically generate three topic-based 1280×720 AI thumbnail designs after each video renders (no MP4 download required); select one and upload it to YouTube from the local dashboard.
- Select male or female narration with `--voice-gender`.
- Choose `bold`, `clean`, or `karaoke` subtitle presets with `SUBTITLE_PRESET` in `.env`.
- Run a demo build without API keys.
- Use the Content Lab to generate topic research plans, improve scripts, audit claims against supplied source material, and convert long-form scripts into standalone Shorts.
- Generate narration with Chatterbox TTS, with automatic Edge TTS fallback when `TTS_ENGINE=chatterbox`.
- Generate images through Cloudflare when configured, with Pollinations as a fallback.
- Search Pixabay for optional stock-video clips.
- Add captions, transitions, background music, and finance-education disclaimers.
- Upload videos to YouTube and optionally send Telegram notifications.
- Run scheduled or manual builds through GitHub Actions.

## Requirements

- Python 3.11 or newer (GitHub Actions uses Python 3.11).
- **FFmpeg and FFprobe** installed and available on your system PATH.
- Internet connection for TTS, image generation, Gemini, Pixabay, and YouTube API calls.

### Install FFmpeg

**Windows (PowerShell):**
```powershell
winget install Gyan.FFmpeg
```
Close and reopen PowerShell after installation, then verify:
```powershell
ffmpeg -version
ffprobe -version
```

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install -y ffmpeg fonts-noto-core
```

**macOS (Homebrew):**
```bash
brew install ffmpeg
```

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/ay23-byte/ai-studio-automation.git
cd ai-studio-automation
python -m venv .venv
```

Activate the environment:

**Windows PowerShell**
```powershell
.\.venv\Scripts\Activate.ps1
```

**macOS/Linux**
```bash
source .venv/bin/activate
```

Install Python dependencies:
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Configure environment variables

The template is named `.env.example` (with a leading dot).

**Windows PowerShell**
```powershell
Copy-Item .env.example .env
```

**macOS/Linux**
```bash
cp .env.example .env
```

Open `.env` and add the credentials needed for the features you intend to use. Do not put real credentials in GitHub.

### 3. Run a demo (no API keys)

```bash
python run.py demo
```

The demo still needs FFmpeg/FFprobe and working access to the configured visual/TTS services used by the build pipeline.

### 4. Generate videos

Create a Short:
```bash
python run.py shorts "Inflation kya hai" --category finance --voice-gender female
```

Create a long-form video:
```bash
python run.py long "Mutual Fund explained" --category finance --minutes 7 --voice-gender male
```

List generated video jobs:
```bash
python run.py list
```

Upload a completed job. Every upload is private, and it is refused until a person has
reviewed that exact file:
```bash
python run.py review --id 1          # writes build/job_1/shared_review_package/review_dashboard.html
# open the dashboard, watch the video, decide every item, download review_decisions.json
python run.py upload --id 1 --decisions review_decisions.json
```
The rules are the ones the GitHub release workflows use
([`docs/SHARED_REVIEW_PACKAGE.md`](docs/SHARED_REVIEW_PACKAGE.md)). Re-rendering the video
or rebuilding the package cancels the approval. `--upload` on `run.py shorts`, `run.py long`
and `run_daily.py` prepares the review package after the build; it no longer uploads.

## Local creator dashboard

Install dependencies and launch the review dashboard:

```powershell
python -m pip install -r requirements.txt
streamlit run dashboard.py
```

The dashboard shows local video jobs, previews available local files, records approval decisions, uploads approved local videos as **private** and unscheduled, lets you (as an explicit, confirmed action) schedule an already-uploaded video for public release using India Standard Time, automatically generates three topic-based thumbnail options after rendering, can regenerate them from the title and script without a local MP4, saves a selected thumbnail, and can upload that thumbnail to an existing YouTube video. Configure `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, and `YT_REFRESH_TOKEN` in your local `.env` file to enable YouTube actions. The dashboard uses a local SQLite database created at runtime. GitHub Actions runners are ephemeral: their SQLite state is not persisted between runs or synced to your PC. Do not commit the database; use explicit export/import or a protected external database service if durable shared state is required.

## Visual editorial review (Milestone 19)

Long-form documentary builds write `visual_review_report.json` and initialize a separate `visual_review_decisions.json` file with every shot set to **pending**. Open the local Streamlit dashboard and select **Visual editorial review** in the Pages navigation to inspect sample frames, narration, visual prompts, chapter, and source metadata.

For each shot, explicitly approve it, reject it with a reason, request a replacement, save reviewer notes, and record whether the source/provenance details were inspected. Decisions are persisted atomically and survive dashboard restarts. New shots are added as pending without resetting existing decisions. If the review JSON is malformed, the dashboard reports the problem and preserves the original file rather than silently replacing it.

Long-form video approval, upload, and public scheduling are blocked until every shot is approved, source review is marked reviewed, and the media file exists. Shorts are unaffected. The audit CLI can enforce the same gate:

```bash
python scripts/audit_documentary.py --input documentary_audit_input.json --visual-review build/job_1/visual_review_report.json
```

Editorial approval and source review are workflow attestations only. They do not prove factual accuracy, historical authenticity, copyright ownership, legal licensing, or presenter lip-sync. Verify facts and asset terms separately before publishing. Existing long-form builds without a visual review manifest must regenerate the review report before they can pass the new publication gate.

## Multi-channel workspaces (Phase 5)

Use the **📺 Channels** dashboard tab to create and edit channel workspaces with a name, handle, niche, default language/category and brand voice. Generate a Shorts or long-form script from the same tab; the selected channel's name, audience and editorial voice are included in the Gemini prompt. Generated scripts are saved as `script_ready` draft jobs and appear in the video review library; they are not rendered or published automatically. Existing SQLite databases are migrated automatically and existing video records are preserved. These profiles organize editorial workflows only: upload and analytics still use the single YouTube OAuth account configured in environment secrets. Separate OAuth accounts are not connected yet.

## YouTube analytics (Phase 4)\n\nOpen the dashboard and select **📊 YouTube analytics**. Choose a date range to view channel lifetime totals, daily views, watch time, average view duration, subscribers gained, likes, comments, and top videos. This is read-only and does not publish or modify videos. Analytics reports can be delayed by YouTube.\n\nThe YouTube OAuth refresh token must include `https://www.googleapis.com/auth/yt-analytics.readonly` and `https://www.googleapis.com/auth/youtube.readonly`. Regenerate the token with the updated `get_refresh_tokens.py` flow, then replace the `YT_REFRESH_TOKEN` GitHub Actions secret (and local `.env` value if used). Never commit credentials.\n\n## Long-form production quality upgrade

Long-form scripts now use a documentary-style structure with a cold open, clear viewer promise, layered chapters, examples, misconceptions, honest retention loops, and a resolved ending. The prompt targets roughly 125-145 spoken words per minute and requests 3-5 chronological, distinct 16:9 visual beats per chapter plus fact-check notes.

The render pipeline turns those visual beats into separate shots instead of holding one image for an entire chapter. Long-form renders now default to **video-first B-roll**: each beat searches Pixabay for a relevant moving clip first, then uses a generated image only when no suitable stock clip is found. Set `LONGFORM_VISUAL_MODE=image_first` to restore image-first behavior. The GitHub Actions `make-longform` form exposes this as the **B-roll source preference** input; choose `video_first` for documentary footage. Add `PIXABAY_API_KEY` under repository Actions secrets for stock-video search. Without that key, the renderer will fall back to generated images, so it cannot guarantee every shot will be a video. Long-form exports use H.264 at CRF 18, 256 kbps AAC audio, varied transitions, and quieter background music to protect narration clarity. Actual output depends on relevant stock availability, provider quotas, the script, TTS voice, and runner; no automatic 4K footage or factual verification is implied.

## Step 1: Automatic video quality inspector

Every newly rendered Short and long-form export receives a local FFmpeg/FFprobe quality scan. The report is saved beside the job build files as `quality_report.json` and linked from the local review dashboard.

Checks include video/audio stream presence, resolution, detected black intervals, long near-silent intervals, repeated sampled frames, and subtitle-file presence/event timing. Reports show a score, warnings and issues. This diagnostic does not claim to judge factual accuracy, visual-to-narration semantic matching, or audience retention. A failed optional scan is logged without deleting the rendered video.

The checks use FFmpeg/FFprobe and the existing Pillow dependency; no paid API is required. Run `python -m unittest discover -s tests -v` to run the regression suite.

## Step 2: Narration-locked visual engine

Long-form storyboard generation now requires at least three structured visual beats per chapter. Every beat must include an exact phrase copied from that chapter's narration, a specific visual prompt, and relevant stock-search keywords. The script is rejected before rendering if anchors are missing, out of order, or prompts/keywords are incomplete.

Both Shorts and long-form image prompts now explicitly include the spoken narration moment so generated imagery is directed at the actual scene. The long-form pipeline defaults to scene-specific Pixabay video search first, with generated images as fallback; Shorts continue to use image-first behavior and try Pixabay only if image generation fails. It no longer substitutes random local clips or repeats the previous scene's media when a scene has no matching asset; the build stops with an actionable error instead.

This prevents known sources of obvious mismatch, but it cannot guarantee that an AI-generated image or a stock search result is semantically perfect. Review the rendered video before publishing. Older saved scripts that use legacy string-only storyboards remain supported, with their previous approximate timing behavior.
## Long-form on-screen presenter

The GitHub Actions long-form workflow automatically selects the matching presenter portrait when you choose the narration voice: **male** uses `assets/presenter/presenter_master.png`, and **female** uses `assets/presenter/presenter_female.png`. Choose `talking_avatar` to insert browser-recorded, audio-synchronized 3D avatar clips for each chapter, `sadtalker` for audio-driven facial animation generated on the runner, `still_cpu` for a lightweight moving portrait, or `off` to disable the presenter. For `talking_avatar`, add one clip per chapter to `assets/presenter_clips/` in the repository before starting the workflow. Name them `presenter_section_01.mp4`, `presenter_section_02.mp4`, and so on. The browser Studio creates WebM; convert each clip to MP4 with FFmpeg before committing for the most reliable GitHub Actions rendering. The clips must match the chapter narration and must not exceed that chapter's narration duration. The workflow runner cannot access files left only on your personal computer. SadTalker runs in a separate Python 3.8 CPU environment on the standard GitHub runner and downloads its pretrained checkpoints during setup. CPU inference can be very slow for a 10–15 minute documentary; it may take substantially longer than the narration itself. The output is a talking-head animation, not a guarantee of perfect phoneme-level synchronization or broadcast-quality realism. Review the rendered video before publishing.

## Content Lab (Phase 3)

Open the local dashboard and select **🧠 Content lab**.

- **Topic research:** generate video ideas, viewer questions, hooks, angles, key points, and search queries. These are AI-generated research plans, not live trend or search-volume data.
- **Script improvement:** select a saved script or paste script JSON to receive an editorial score, strengths, prioritized issues, and an improved script.
- **Claim audit:** optionally paste source excerpts or URLs. The tool marks claims as supported by supplied material, contradicted by supplied material, or needing verification. Without source material, it does not claim that facts have been externally verified.
- **Long-form to Shorts:** choose a saved long-form script and generate up to eight independent Shorts scripts. Download the result as JSON.

Generated Content Lab results are also saved locally under `output/content_lab/`. Gemini API access is required for these AI features.


### Expanded documentary footage libraries

When a scene has no suitable Pixabay result, the renderer can search additional public libraries in this order: NASA Image and Video Library, Wikimedia Commons, and Internet Archive. NASA, Wikimedia Commons and Internet Archive discovery do not require a paid account. 

For Wikimedia Commons, the asset's file-specific licence and attribution are captured in a `.source.json` sidecar. Other providers also receive a provenance sidecar with the asset page and rights-review link where available. **A search result is not automatically cleared for commercial reuse**: review each asset's rights statement, third-party credits, attribution and any restrictions before publishing. Internet Archive contains material with differing rights status; do not assume every item is public domain. NASA media can also contain third-party material or identifiable people/trademarks, so check item guidance.

Mixkit is listed as a manual research source only because it does not expose a supported public stock-video search API. The project intentionally does not scrape its website or bypass access controls. Browse [Mixkit free stock video](https://mixkit.co/free-stock-video/) and manually add only clips whose licence permits your intended use.

All discovered clips are downloaded only after HTTPS checks and validated with FFprobe for minimum file size, duration and resolution. If no real clip is found, the existing generated-image fallback remains available. The engine does not guarantee a real video for every narration beat, and it does not download copyrighted news-broadcast footage from YouTube, AP, Reuters or other broadcasters.

### Long-form visual fallback order

In the default `video_first` mode, each B-roll beat now tries these sources in order:

1. Relevant Pixabay stock video (requires `PIXABAY_API_KEY`).
2. Optional Replicate text-to-video generation (requires `AI_VIDEO_ENABLED=1`, `REPLICATE_API_TOKEN`, and `REPLICATE_VIDEO_MODEL=owner/model`).
3. Existing generated-image fallback (Cloudflare Workers AI, then Pollinations).

AI video is **off by default**. The code does not enable it just because a token exists. Replicate model input fields differ, so the default request uses `prompt`, `duration=5`, and `aspect_ratio=16:9`; set `REPLICATE_VIDEO_INPUT_JSON` to a JSON object matching the chosen model's documented input schema when needed. The prompt field is replaced with the scene prompt. The provider is polled until completion (up to `AI_VIDEO_TIMEOUT_SECONDS`) and the downloaded file is checked with ffprobe before it enters the edit. Set `AI_VIDEO_ENABLED=0` to disable it.

**Cost warning:** Replicate is not a guaranteed free service or free-credit entitlement. Only enable this fallback after checking your account's current credits and the selected model's pricing. A configured API token may incur charges when predictions run. The workflow does not claim that free credits are available.

## Universal documentary production

The long-form workflow is topic-agnostic. Set **Category** to `auto` (the default) and enter any subject: history, science, physics, space, nature, biography, geography, culture, technology, mystery, economics, current affairs, or a topic outside those examples. Gemini is instructed to infer the most suitable category from the topic and research, then tailor the documentary's terminology, story structure, visual direction and verification standards. You may also enter a custom category when you want to override automatic inference.

The workflow's **Gemini vision semantic screen** is an optional switch. When enabled, it compares sampled rendered frames with the corresponding narration and visual prompt; it consumes additional Gemini model usage and does not replace human editorial review.

AI video generation is available through an explicitly gated Replicate adapter. It is OFF by default. To enable it for a workflow run, set the repository secret `REPLICATE_API_TOKEN`, then add these repository Actions variables: `REPLICATE_VIDEO_MODEL` (`owner/model`), and `REPLICATE_VIDEO_INPUT_JSON` (the selected model's exact input JSON, using `{{prompt}}` and optionally `{{aspect_ratio}}` placeholders). If using a version-specific model endpoint, also set `REPLICATE_VIDEO_VERSION`. In the `make-longform` workflow, explicitly turn on **Enable Replicate text-to-video generation** and choose a maximum clip count. That opt-in disables `FREE_ONLY_MODE` for that run and can incur provider charges; the clip cap is enforced in code. Keep it off for zero-cost runs. The adapter polls Replicate, downloads and FFprobe-validates returned video, and writes an AI-disclosure/source sidecar. Model input schemas and model licensing vary; review the selected model's README and terms before enabling. AI-generated video must not replace requested archival evidence.

## API keys and when they are needed

| Variable | Purpose | Required when |
|---|---|---|
| `GEMINI_API_KEY` | Generate scripts and SEO text; get it at [Google AI Studio](https://aistudio.google.com/) | Creating a new AI script (not the built-in demo) |
| `GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS` | Primary model and comma-separated fallback model IDs | Optional; defaults are provided. Temporary 503 outages can still affect all providers/models simultaneously. |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | Cloudflare Workers AI image generation | Using Cloudflare image generation; optional if fallback works |
| `PIXABAY_API_KEY` | Search for stock video clips at [Pixabay API](https://pixabay.com/api/docs/) | Optional stock-footage source |
| `AI_VIDEO_ENABLED`, `REPLICATE_API_TOKEN`, `REPLICATE_VIDEO_MODEL` | Optional Replicate text-to-video fallback | Only when explicitly enabled and configured; model usage may cost money |
| `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` | YouTube Data API OAuth credentials | Uploading to YouTube |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram build/upload/failure notifications | Optional |
| `PUBLISH_AT_IST` | Scheduled YouTube publishing time in 24-hour IST format, e.g. `19:00` | Optional |

All API providers have their own quotas and terms; free-tier availability and limits can change.

## GitHub Actions automation

The repository includes:

- `.github/workflows/validate.yml` — Python syntax validation on pushes and pull requests.

- `.github/workflows/make_shorts.yml` — manual Shorts generation and a daily scheduled run.
- `.github/workflows/make_longform.yml` — manual long-form generation.

To enable cloud builds, open **GitHub → repository → Settings → Secrets and variables → Actions** and add the secrets used by the workflows:

- `GEMINI_API_KEY`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`
- `PIXABAY_API_KEY`
- `REPLICATE_API_TOKEN` (optional; only if you enable AI video and have checked available credits)
- `YT_CLIENT_ID`
- `YT_CLIENT_SECRET`
- `YT_REFRESH_TOKEN`
- `TELEGRAM_BOT_TOKEN` (optional)
- `TELEGRAM_CHAT_ID` (optional)

The Shorts workflow runs daily at 09:00 IST. Build workflows never upload: each run produces a review package as an Actions artifact. After you have reviewed it in the dashboard, the separate release workflow uploads that exact file as **private** and unscheduled. Publishing is done by you in YouTube Studio. See [docs/GITHUB_ACTIONS_START_HERE.md](docs/GITHUB_ACTIONS_START_HERE.md) and [docs/SHARED_REVIEW_PACKAGE.md](docs/SHARED_REVIEW_PACKAGE.md).

## Project structure

```text
ai-studio-automation/
├── .github/workflows/
│   ├── make_shorts.yml
│   └── make_longform.yml
├── core/
│   ├── config.py
│   ├── db.py
│   ├── script_engine.py
│   ├── voice.py
│   ├── images.py
│   ├── editor_shorts.py
│   ├── editor_longform.py
│   ├── pipeline.py
│   ├── uploader.py
│   ├── thumbnail.py
│   └── notify.py
├── assets/
│   ├── music/       # Add licensed/royalty-free MP3 files here
│   └── clips/       # Optional local MP4 fallback footage
├── output/          # Generated videos (created automatically)
├── build/           # Temporary render files (created automatically)
├── db/              # SQLite job database (created automatically)
├── .env.example
├── env.example      # Legacy copy retained for compatibility
├── .gitignore
├── requirements.txt
├── run.py
├── run_daily.py
└── topics.json
```

## Notes and safety

- The demo command uses a built-in sample script; no Gemini key is needed for script generation in demo mode.
- Generated videos are uploaded as **private** by default. Keep this setting until you have checked the correct YouTube channel and video.
- The local `.env` file and SQLite database are not intended to be committed. GitHub Actions explicitly saves its database state.
- Add only music and footage that you have permission to use. YouTube Audio Library and appropriately licensed stock sources are good starting points.
- Finance videos include an educational-information disclaimer; this is not financial advice.
- YouTube OAuth credentials are tied to the Google account/channel authorization. Verify the target channel before uploading.


## Step 4: YouTube SEO Studio

Open **YouTube SEO Studio** from the Streamlit dashboard sidebar, select a saved script/video job, and generate a metadata package using the existing Gemini API configuration. Review and edit title options, description, tags, up to three hashtags, pinned comment, thumbnail text, and chapter lines, then save the package to the job's `script_json`. Uploading an approved video uses the saved SEO description and tags when present. Titles are limited to 100 characters, descriptions to 5,000 characters, tags to 30 items / 500 characters, and the pinned comment to 1,000 characters. Long-form chapter starts are estimates based on section narration word counts and the script's estimated runtime—not measured from the final MP4—so verify every timestamp against the rendered video. Shorts do not receive chapters. Keyword suggestions are AI-generated; this feature does not claim live search volume, rankings or trend data. No additional paid service is introduced; Gemini API quota still applies.

Run `python -m unittest discover -s tests -v` to test metadata limits and chapter ordering.


## Step 6: Performance-Based Content Improvement

Open **📊 YouTube analytics** in the local dashboard, choose a reporting window, and click **Analyze channel performance**. The dashboard reads channel totals, daily views, and up to 25 top videos from the YouTube Analytics API; it fetches public titles for those video IDs and calculates engagement per view (likes + comments + shares divided by views), median comparisons, average view duration and descriptive recommendations for future experiments. These metrics are period-specific, not lifetime performance. Average view duration is not audience-retention percentage, and the tool does not claim causal explanations or private competitor analytics. It does not automatically rewrite or publish scripts. Your OAuth token must include `youtube.readonly` and `yt-analytics.readonly`; no new paid API is introduced.

Run `python -m unittest discover -s tests -v` to test metric calculations and empty-data handling.


## Hindi/Hinglish documentary channel (10–15 minutes)

The long-form GitHub Actions workflow is configured for documentary production:
- Hindi/Hinglish narration, with a 12-minute default and 10, 12 or 15-minute choices.
- Topic categories include geopolitics, history, science, technology, economics, India, mysteries and education.
- Male/female narration selection.
- YouTube upload is **off by default**. Review the rendered artifact and approve it before enabling upload.
- See [the Documentary Channel Production Standard](docs/DOCUMENTARY_CHANNEL_STANDARD.md) for research, claim verification, visual rights, presenter ethics and release checks.

### Run a documentary locally

```bash
python run.py long "How does a global oil shock affect India?" --category geopolitics --minutes 12 --language hinglish --voice-gender male
```

### CPU-friendly documentary presenter

The long-form pipeline offers four presenter modes: `sadtalker`, `talking_avatar`, `still_cpu` and `off`. SadTalker generates audio-driven face/mouth animation from the exact chapter narration segment. GitHub Actions provisions SadTalker in a separate Python 3.8 environment, installs CPU PyTorch and downloads the official `SadTalker_V0.0.2_256.safetensors` and mapping checkpoints. The adapter validates video/audio stream presence, resolution, frame rate and A/V duration alignment, and records provenance beside the clip. CPU inference can be extremely slow; use `SADTALKER_DEVICE=cuda` on a compatible GPU workstation. The hosted workflow defaults to `still_cpu` because SadTalker CPU jobs may exceed practical runtime limits; select `sadtalker` explicitly when needed. `still_cpu` is a moving portrait and is **not lip-sync**. The browser-based `talking_avatar` mode uses the exact recorded narration stream, but Hindi/Hinglish mouth shapes can be approximate. No automated duration or stream check proves phoneme-level synchronization: inspect the presenter's mouth against the narration before approval. The male/female narration selection chooses the corresponding portrait. Use original/fictional presenter imagery or ensure you have permission to use it. See [the presenter asset specification](docs/PRESENTER_ASSET_SPEC.md) and [the documentary production standard](docs/DOCUMENTARY_CHANNEL_STANDARD.md).


## Free browser-based talking 3D presenter

The optional `avatar_studio/` companion uses the MIT-licensed [Talking Avatar SDK](https://github.com/InterviewFlowAI/talking-avatar). It includes built-in male and female 3D presenters and animates their mouths from a narration audio file in the browser. No avatar API key, account or paid avatar service is required.

Start a local static server from the repository root:

```powershell
python -m http.server 8765
```

Open [http://localhost:8765/avatar_studio/](http://localhost:8765/avatar_studio/) in Chrome or Edge. Choose a presenter, upload narration, load the avatar, then use **Record avatar clip**. In the browser sharing prompt, select the current tab and enable tab audio. Stop and save the recording when narration finishes. The output is WebM; convert it to MP4 with FFmpeg:

```powershell
ffmpeg -y -i talking_presenter.webm -c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 192k talking_presenter.mp4
```

This studio is a companion clip creator; it does not yet automatically replace every presenter shot in the main long-form pipeline. The SDK does not convert portrait photos into talking videos, and its lip-sync model is English-trained, so Hindi/Hinglish mouth shapes may be approximate. It animates the face (mouth, blinks and brows), not a full body. Browser recording requires a user permission step. Existing presenter modes remain unchanged.


### Talking Avatar clips in long-form videos

The browser Talking Avatar Studio can now feed the existing long-form editor. This is an **opt-in clip-import workflow**: render one avatar clip per chapter from that chapter's exact narration audio, then place the files in `assets/presenter_clips/` using these names:

- `presenter_section_01.mp4`
- `presenter_section_02.mp4`
- Continue numbering to match the script's `sections` order. WebM files are also accepted.

Set the following in your local `.env`:

```dotenv
PRESENTER_ENABLED=1
PRESENTER_MODE=talking_avatar
AVATAR_CLIPS_DIR=assets/presenter_clips
```

The long-form pipeline inserts each chapter's avatar clip at that chapter's start, then uses the existing B-roll timeline for the remaining chapter duration. The final editor mutes the clip's embedded audio and uses the single continuous generated narration track, avoiding doubled audio. Clips longer than their chapter narration are rejected rather than silently desynchronizing lip-sync. Missing chapter clips produce a clear error. To return to the existing still presenter, set `PRESENTER_MODE=still_cpu`.

The browser studio remains a separate browser-based renderer: this integration consumes its exported clips; it does not launch a browser or generate the avatar automatically inside Python/GitHub Actions. The browser export requires the user to select tab audio and record the chapter narration. Hindi/Hinglish lip-sync quality can be approximate.


## End-to-end workflow audit (research → final MP4)

A deterministic release-audit layer is available for the documentary pipeline. It checks
research-source metadata and claim evidence links, storyboard narration/timestamp coverage,
footage scene coverage and provenance/licence-review flags, presenter chapter durations,
and (when an MP4 is supplied) the existing FFmpeg/FFprobe export checks. It deliberately
does **not** claim that a source proves a claim, that an asset is legally reusable, that
a visual is semantically relevant, or that equal presenter duration proves lip-sync.
Human editorial review remains required.

See [End-to-End Workflow Audit](docs/END_TO_END_WORKFLOW_AUDIT.md) for the input schema
and commands. Run:

```bash
python -m unittest discover -s tests -v
python scripts/audit_documentary.py --input documentary_audit_input.json
python scripts/audit_documentary.py --input documentary_audit_input.json --video output/final_documentary.mp4 --subtitles output/subtitles.ass
```


## Milestone 20: AI visual-to-narration semantic screening

Long-form visual review manifests can optionally run a Gemini vision screen against up to three sampled frames per shot. The model compares visible content with the shot narration and visual prompt, returning a conservative `match`, `mismatch`, or `uncertain` result, a score, visible evidence and concerns. Results appear in the **Visual Editorial Review** page and are saved in `visual_review_report.json`.

Enable it explicitly in `.env`:

```dotenv
VISUAL_SEMANTIC_CHECK=1
VISUAL_SEMANTIC_MAX_SHOTS=40
```

The screen uses the existing `GEMINI_API_KEY` and `GEMINI_MODEL`. It is opt-in because it makes an additional vision-model request per shot and may incur API usage. Set the maximum shot count to control the per-build request budget. If the key, frames or model are unavailable, the report records that state rather than claiming a match.

**Publication behavior:** when semantic screening is enabled, any shot that is not explicitly returned as `match` blocks the long-form publication gate until the issue is resolved and the report is regenerated. A `match` never approves a shot: a human must still approve it in the editorial review page and review its source metadata. This model cannot establish factual truth, historical authenticity, identity, copyright ownership, licensing, or lip-sync.


## Milestone 21: Cinematic consistency and shot-quality control

Long-form documentary rendering now applies one consistent colour-grade preset to every image and video segment, instead of using different hard-coded adjustments for stills and B-roll. Configure `DOCUMENTARY_COLOR_GRADE` as `neutral`, `cinematic` (default), `warm`, `cool` or `archive`. Shorts rendering is unchanged.

Each long-form build also writes `visual_consistency_report.json` and adds per-shot technical metrics to `visual_review_report.json`. Sampled frames are checked for source resolution, exposure, contrast, saturation, clipped highlights/shadows and a sharpness proxy. Shots with severe technical failures block the editorial publication gate; relative colour/exposure outliers are flagged for human review.

These are deterministic image-quality diagnostics, not a replacement for a colourist or a semantic/factual review. Source footage may legitimately vary by scene; use the report to find unintended inconsistencies, not to force every scene to look identical.


## Milestone 22: Documentary audio mastering and listening QA

Long-form documentary mixes now receive a final FFmpeg loudness master after narration/music mixing and ducking. Defaults target **-16 LUFS integrated loudness**, **-1.5 dBFS true peak**, and **11 LU loudness range**. Override with `DOCUMENTARY_TARGET_LUFS`, `DOCUMENTARY_TRUE_PEAK_DBFS` and `DOCUMENTARY_TARGET_LRA`. Shorts audio rendering is unchanged.

After rendering, the system writes `audio_quality_report.json` with measured integrated loudness, true peak, loudness range, clipped-sample count and long near-silent intervals. The main `quality_report.json` also includes the audio mastering result.

**Human listening review is required before long-form approval or upload.** Open the dashboard's **Documentary audio mastering review** section, listen to the complete export on headphones and speakers, inspect any warnings, and mark the listening review complete with optional notes. Blocking peak/clipping failures cannot be waived by the listening-review button; fix the audio and rebuild. A passing meter report is not a substitute for listening.


## Milestone 23: Presenter realism and synchronization QA

Audio-driven presenter clips now receive a technical A/V validation report covering embedded audio/video streams, resolution, frame rate, clip duration and audio/video duration delta. SadTalker checkpoint validation now matches the official model files downloaded by the GitHub Actions workflow, and the adapter records a hash of the exact narration segment used to drive the animation. The editorial review page identifies the actual presenter mode, including when SadTalker falls back to a still portrait.

A passing A/V check means the streams and durations align; it does **not** certify mouth-shape accuracy, identity, likeness or phoneme-level lip-sync. Human review remains mandatory. The `still_cpu` presenter is explicitly marked as not lip-synced.


## Milestone 24: Research verification, citations and source rights

Every new long-form build writes `research_verification_report.json` and a persistent `research_review_decisions.json`. The dossier includes source title, publisher, date, URL, whether the URL was among the retrieved discovery leads, claim-level source IDs and the evidence note supplied by the script model.

Open **Research, citations & rights verification** in the Streamlit Pages navigation. A human reviewer must open each original source, record inspection notes, verify every claim, resolve disputed/unsupported claims, and attest that the complete narration has no material factual claims missing from the claim ledger. A URL that parses correctly or matches a GDELT discovery lead is not proof that the source supports a claim; this workflow does not automatically fetch/validate article contents or establish truth.

The **Visual Editorial Review** page now has a separate per-shot copyright/licence decision, licence basis, attribution and reviewer notes. A source/provenance check is not a rights clearance. Mark rights reviewed only after checking the actual asset terms, commercial-use restrictions, attribution requirements and any permissions. The editorial decision store migrates version-1 records to version 2 while preserving old decisions; all migrated shots start with rights review pending.

Long-form publication now requires all of the following: approved shot reviews, reviewed provenance, documented rights review for every shot, no unresolved visual/semantic/presenter technical failures, a completed audio QA report plus human listening review, and a human-verified claim ledger with coverage attestation. These controls are workflow gates, not legal advice or independent certification.


## Milestone 25: End-to-end production testing and release certification

A long-form documentary must now pass an explicit release-certification step before approval, upload or public scheduling. The certifier checks the final MP4 delivery profile (video/audio streams, landscape resolution, frame rate, duration and stereo sample rate), the visual consistency report, the final video quality report, all human visual/source/rights decisions, the audio listening review, the claim-level research review and the presenter/semantic failure states.

The certifier writes `release_certificate.json` with a unique certificate ID, check results, blockers, warnings, video metadata and SHA-256 hashes of the final MP4 plus all required review/QA reports and decision stores. If any required gate is incomplete, the certificate is `BLOCKED`. If a video or report changes after certification, upload is blocked until the build is certified again.

You can certify from the dashboard under **Documentary release certification**, or run:

```bash
python scripts/certify_release.py --build-dir "<BUILD_DIR>/job_<ID>" --video "<OUTPUT_DIR>/longform_<ID>.mp4"
```

The certificate is an internal production checklist and artifact-integrity record, not a legal, factual, historical or scientific guarantee. The FFmpeg smoke-test suite also creates a real sample MP4 and runs the release certifier end to end.


## Universal free documentary production stack

See [Universal Free Documentary Media Stack](docs/UNIVERSAL_FREE_DOCUMENTARY_MEDIA_STACK.md) for the provider-agnostic media strategy, free tools, licensing rules, visual QA workflow and limitations.
