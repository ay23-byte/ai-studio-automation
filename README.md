# AI Studio - YouTube Video Creator

Automated Hindi/Hinglish YouTube video creator for **Shorts (9:16)** and **long-form videos (16:9)**. It generates scripts with Gemini, creates visuals, synthesizes narration with Edge TTS, renders videos with FFmpeg, and can upload them to YouTube.

## Features

- Create Shorts and long-form videos from a topic.
- Run a demo build without API keys.
- Generate narration with Microsoft Edge TTS.
- Generate images through Cloudflare when configured, with Pollinations as a fallback.
- Search Pixabay for optional stock-video clips.
- Add captions, transitions, background music, and finance-education disclaimers.
- Upload videos to YouTube and optionally send Telegram notifications.
- Run scheduled or manual builds through GitHub Actions.

## Requirements

- Python 3.10 or newer (GitHub Actions uses Python 3.12).
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
python run.py shorts "Inflation kya hai" --category finance
```

Create a long-form video:
```bash
python run.py long "Mutual Fund explained" --category finance --minutes 7
```

List generated video jobs:
```bash
python run.py list
```

Upload a completed job (keep `PRIVACY=private` while testing):
```bash
python run.py upload --id 1
```

## API keys and when they are needed

| Variable | Purpose | Required when |
|---|---|---|
| `GEMINI_API_KEY` | Generate scripts and SEO text; get it at [Google AI Studio](https://aistudio.google.com/) | Creating a new AI script (not the built-in demo) |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | Cloudflare Workers AI image generation | Using Cloudflare image generation; optional if fallback works |
| `PIXABAY_API_KEY` | Search for stock video clips at [Pixabay API](https://pixabay.com/api/docs/) | Optional stock-footage source |
| `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` | YouTube Data API OAuth credentials | Uploading to YouTube |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram build/upload/failure notifications | Optional |
| `PUBLISH_AT_IST` | Scheduled YouTube publishing time in 24-hour IST format, e.g. `19:00` | Optional |

All API providers have their own quotas and terms; free-tier availability and limits can change.

## GitHub Actions automation

The repository includes:

- `.github/workflows/make_shorts.yml` — manual Shorts generation and a daily scheduled run.
- `.github/workflows/make_longform.yml` — manual long-form generation.

To enable cloud builds, open **GitHub → repository → Settings → Secrets and variables → Actions** and add the secrets used by the workflows:

- `GEMINI_API_KEY`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`
- `PIXABAY_API_KEY`
- `YT_CLIENT_ID`
- `YT_CLIENT_SECRET`
- `YT_REFRESH_TOKEN`
- `TELEGRAM_BOT_TOKEN` (optional)
- `TELEGRAM_CHAT_ID` (optional)

The Shorts workflow runs daily at 09:00 IST. Its scheduled upload step is enabled by the workflow configuration and currently sets `PRIVACY=private`. Review the workflow before changing publishing privacy or schedule. Long-form runs are manually triggered.

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
