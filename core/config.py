import os
from pathlib import Path

ROOT = Path(__file__).parent.parent

GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL     = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
PIXABAY_API_KEY  = os.getenv("PIXABAY_API_KEY", "")
CF_ACCOUNT_ID    = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
CF_API_TOKEN     = os.getenv("CLOUDFLARE_API_TOKEN", "")
POLLINATIONS_GAP = float(os.getenv("POLLINATIONS_GAP", "16"))

YT_CLIENT_ID     = os.getenv("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

VOICE_HINDI   = os.getenv("VOICE_HINDI",   "hi-IN-MadhurNeural")
VOICE_ENGLISH = os.getenv("VOICE_ENGLISH", "en-IN-PrabhatNeural")
VOICE_FEMALE  = os.getenv("VOICE_FEMALE",  "hi-IN-SwaraNeural")

CHANNEL_NAME   = os.getenv("CHANNEL_NAME", "Power Codex")
PRIVACY        = os.getenv("PRIVACY", "private")
PUBLISH_AT_IST = os.getenv("PUBLISH_AT_IST", "")

DB_PATH    = ROOT / "db" / "studio.sqlite"
OUTPUT_DIR = ROOT / "output"
ASSETS_DIR = ROOT / "assets"
MUSIC_DIR  = ASSETS_DIR / "music"
CLIPS_DIR  = ASSETS_DIR / "clips"
BUILD_DIR  = ROOT / "build"

for _d in (OUTPUT_DIR, BUILD_DIR, MUSIC_DIR, CLIPS_DIR, ROOT/"db"):
    _d.mkdir(parents=True, exist_ok=True)

IMAGE_STYLE = (
    "Premium cinematic 3D illustration for a financial education video. "
    "Dark navy background with gold and teal accents. Dramatic soft lighting. "
    "Clean simple symbolic composition. No text, letters, numbers, logos, charts with data, "
    "human faces, or real people. Scene: "
)
