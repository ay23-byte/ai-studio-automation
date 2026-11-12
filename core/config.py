import os
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _path_from_env(name, default):
    """Resolve an optional path override; relative values are repo-relative."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    candidate = Path(raw).expanduser()
    return candidate if candidate.is_absolute() else ROOT / candidate


def env_flag(name, default=False):
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}

GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
# Default model ids live in core.gemini (the single place they are written); an
# empty GEMINI_MODEL (an unset Actions variable expands to "") falls back to it.
# GEMINI_PROFILE (default | longform) selects which default chain applies.
from core.gemini import default_model as _gemini_default_model, configured_fallbacks as _gemini_fallbacks
GEMINI_MODEL     = os.getenv("GEMINI_MODEL", "").strip() or _gemini_default_model()
GEMINI_FALLBACK_MODELS = _gemini_fallbacks()
PIXABAY_API_KEY  = os.getenv("PIXABAY_API_KEY", "")
COVERR_API_KEY   = os.getenv("COVERR_API_KEY", "")
NARA_API_KEY     = os.getenv("NARA_API_KEY", "")
EUROPEANA_API_KEY = os.getenv("EUROPEANA_API_KEY", "")
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
VOICE_ENGLISH_FEMALE = os.getenv("VOICE_ENGLISH_FEMALE", "en-IN-NeerjaNeural")
VOICE_FEMALE  = os.getenv("VOICE_FEMALE",  "hi-IN-SwaraNeural")

CHANNEL_NAME   = os.getenv("CHANNEL_NAME", "Engineering Physics")
# Expected upload destination. When either value is set, the uploader verifies
# the authenticated channel before any upload and refuses a mismatch.
YT_CHANNEL_HANDLE = os.getenv("YT_CHANNEL_HANDLE", "@engineeringphysics-n5g").strip()
YT_CHANNEL_ID     = os.getenv("YT_CHANNEL_ID", "").strip()
SUBTITLE_PRESET = os.getenv("SUBTITLE_PRESET", "bold").strip().lower()
PRIVACY        = os.getenv("PRIVACY", "private")
PUBLISH_AT_IST = os.getenv("PUBLISH_AT_IST", "")

def _running_under_test_runner():
    """True for ``python -m unittest`` / ``pytest`` invocations.

    Test runs must never open (or migrate) the operator's real job database.
    """
    import sys
    if os.getenv("PYTEST_VERSION") or os.getenv("PYTEST_CURRENT_TEST"):
        return True
    argv0 = str(sys.argv[0]).replace("\\", "/").lower() if sys.argv and sys.argv[0] else ""
    # ``python -m unittest`` rewrites argv[0] to "<python> -m unittest".
    return (argv0.endswith(("/unittest/__main__.py", "/pytest/__main__.py", "/pytest", "/py.test",
                            "-m unittest", "-m pytest"))
            or argv0 in {"pytest", "py.test"})


def _default_db_path():
    if _running_under_test_runner() and not env_flag("AI_STUDIO_TESTS_USE_REAL_DB"):
        import tempfile
        path = Path(tempfile.gettempdir()) / f"ai_studio_tests_{os.getpid()}" / "studio.sqlite"
        # Exported so helper subprocesses started by tests inherit the sandbox.
        os.environ.setdefault("AI_STUDIO_DB_PATH", str(path))
        return path
    return ROOT / "db" / "studio.sqlite"


# Runtime locations can be redirected (tests, CI sandboxes, a second drive)
# without touching the repository copy of the database or outputs.
DB_PATH    = _path_from_env("AI_STUDIO_DB_PATH", _default_db_path())
OUTPUT_DIR = _path_from_env("AI_STUDIO_OUTPUT_DIR", ROOT / "output")
ASSETS_DIR = ROOT / "assets"
MUSIC_DIR  = ASSETS_DIR / "music"
CLIPS_DIR  = ASSETS_DIR / "clips"
BUILD_DIR  = _path_from_env("AI_STUDIO_BUILD_DIR", ROOT / "build")
STATE_DIR  = _path_from_env("AI_STUDIO_STATE_DIR", ROOT / "state")
LOG_DIR    = _path_from_env("AI_STUDIO_LOG_DIR", OUTPUT_DIR / "logs")

for _d in (OUTPUT_DIR, BUILD_DIR, MUSIC_DIR, CLIPS_DIR, DB_PATH.parent):
    _d.mkdir(parents=True, exist_ok=True)

IMAGE_STYLE = (
    "Premium cinematic 3D illustration for a financial education video. "
    "Dark navy background with gold and teal accents. Dramatic soft lighting. "
    "Clean simple symbolic composition. No text, letters, numbers, logos, charts with data, "
    "human faces, or real people. Scene: "
)
