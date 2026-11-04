# AI Studio - YouTube Video Creator

Free, fully automated YouTube video creator.
Supports **Shorts** (9:16) and **Long-form** (16:9).

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in .env with your keys

# Test without any API keys (uses demo script)
python run.py demo

# Generate a Shorts video
python run.py shorts "Inflation kya hai" --category finance

# Generate a long-form video
python run.py long "Mutual Fund explained" --minutes 7

# List all built videos
python run.py list

# Upload video #1 to YouTube
python run.py upload --id 1
```

## API keys needed

| Key | Where to get | Cost |
|-----|-------------|------|
| GEMINI_API_KEY | aistudio.google.com | Free |
| CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN | dash.cloudflare.com > Workers AI | Free (~170 images/day) |
| PIXABAY_API_KEY | pixabay.com/api/docs | Free |
| YT_CLIENT_ID + YT_CLIENT_SECRET + YT_REFRESH_TOKEN | Google Cloud Console | Free |
| TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID | @BotFather on Telegram | Free |

## File structure

```
ai-studio/
├── core/
│   ├── config.py        # All settings
│   ├── db.py            # SQLite database
│   ├── script_engine.py # Gemini script generation
│   ├── voice.py         # Edge TTS voice synthesis
│   ├── images.py        # Cloudflare/Pollinations AI images + Pixabay
│   ├── editor_shorts.py # 9:16 Shorts editing
│   ├── editor_longform.py # 16:9 long-form editing
│   ├── pipeline.py      # Connects everything
│   ├── uploader.py      # YouTube upload
│   ├── thumbnail.py     # Thumbnail generation
│   └── notify.py        # Telegram alerts
├── assets/
│   ├── music/           # Add royalty-free .mp3 tracks here
│   └── clips/           # Add backup .mp4 footage here
├── output/              # Finished videos saved here
├── build/               # Temporary build files
├── db/                  # SQLite database
├── run.py               # Main CLI
├── requirements.txt
└── .env.example
```

## Notes

- The demo runs without any API keys
- Videos upload as "private" until you change PRIVACY=public
- Finance videos show a "Sirf educational jaankari" disclaimer automatically
- Background music: add .mp3 files to assets/music/ (use YouTube Audio Library)
- Backup footage: add .mp4 files to assets/clips/ (use Pixabay, Mixkit, or Coverr)
