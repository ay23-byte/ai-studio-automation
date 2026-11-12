"""Environment health check (`python run.py doctor`) – no network, no secrets printed."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from core import config as cfg


def run_health_check() -> dict:
    from core import ffmpeg_utils, fonts, llm, notify
    checks = []

    def add(name, status, detail, fix=""):
        checks.append({"check": name, "status": status, "detail": detail, "fix": fix})

    tools = ffmpeg_utils.tools_available()
    add("ffmpeg", "PASS" if tools.get("ffmpeg") else "FAIL",
        shutil.which(ffmpeg_utils.ffmpeg_bin()) or "not found on PATH", "Install FFmpeg and reopen the terminal.")
    add("ffprobe", "PASS" if tools.get("ffprobe") else "FAIL",
        shutil.which(ffmpeg_utils.ffprobe_bin()) or "not found on PATH", "Install FFmpeg (includes ffprobe).")
    if tools.get("ffmpeg"):
        add("ffmpeg subtitles (libass)", "PASS" if ffmpeg_utils.has_filter("subtitles") else "FAIL",
            "available" if ffmpeg_utils.has_filter("subtitles") else "filter missing",
            "Use a full FFmpeg build with libass.")
        add("ffmpeg xfade transitions", "PASS" if ffmpeg_utils.xfade_supported() else "WARN",
            "available" if ffmpeg_utils.xfade_supported() else "missing; hard cuts will be used",
            "Upgrade to FFmpeg 4.3 or newer for crossfades.")

    support = fonts.devanagari_support()
    add("Latin caption font", "PASS" if support.get("latin_font") else "WARN",
        support.get("latin_font") or "no font found", "Restore assets/fonts/ or set SUBTITLE_FONTS_DIR.")
    add("Devanagari caption font", "PASS" if support.get("devanagari_font") else "WARN",
        support.get("devanagari_font") or "no font found; Hindi captions may render as boxes",
        "Restore assets/fonts/NotoSansDevanagari-*.ttf.")
    add("Pillow complex text shaping", "PASS" if support.get("pillow_complex_shaping") else "WARN",
        "libraqm available" if support.get("pillow_complex_shaping")
        else "libraqm missing: Hindi text on thumbnails/slates may not join correctly "
             "(burned-in captions are unaffected)",
        "Install libraqm (apt install libraqm0) for Hindi thumbnails.")

    try:
        from core import db
        integrity = db.integrity_check()
        add("database", "PASS" if integrity == "ok" else "FAIL", f"{db.DB_PATH}: {integrity}",
            "Restore the latest file from db/backups/.")
    except Exception as exc:
        add("database", "FAIL", f"{type(exc).__name__}: {exc}", "Check AI_STUDIO_DB_PATH and file permissions.")

    providers = llm.provider_status()
    configured = [p["provider"] for p in providers if p["configured"]]
    add("script model", "PASS" if configured else "WARN",
        ("configured: " + ", ".join(configured)) if configured
        else "no text provider configured; only pre-written scripts and the demo can be built",
        "Set GEMINI_API_KEY (free tier) and optionally GROQ_API_KEY / OPENROUTER_API_KEY as fallbacks.")
    if configured and len(configured) == 1:
        add("script model fallback", "WARN", f"only {configured[0]} is configured; an outage stops script generation",
            "Add one free fallback key, e.g. GROQ_API_KEY.")

    add("stock footage key", "PASS" if cfg.PIXABAY_API_KEY else "WARN",
        "PIXABAY_API_KEY set" if cfg.PIXABAY_API_KEY else "PIXABAY_API_KEY missing; relying on public archives and generated images",
        "Create a free Pixabay API key.")
    add("offline voice fallback", "PASS" if shutil.which("espeak-ng") else "WARN",
        shutil.which("espeak-ng") or "espeak-ng not installed; narration needs Edge TTS to be reachable",
        "Install espeak-ng.")

    youtube = all(str(v or "").strip() for v in (cfg.YT_CLIENT_ID, cfg.YT_CLIENT_SECRET, cfg.YT_REFRESH_TOKEN))
    add("YouTube credentials", "PASS" if youtube else "WARN",
        "client id, secret and refresh token are set" if youtube else "not configured; uploads are disabled",
        "Run get_refresh_tokens.py and set YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN.")
    target = cfg.YT_CHANNEL_ID or cfg.YT_CHANNEL_HANDLE
    add("YouTube channel lock", "PASS" if target else "WARN",
        f"uploads restricted to {target}" if target else "no YT_CHANNEL_ID/YT_CHANNEL_HANDLE; channel is not verified",
        "Set YT_CHANNEL_ID to the channel's UC… id.")
    public = os.getenv("ALLOW_PUBLIC_PUBLISH", "").strip().lower() in {"1", "true", "yes"}
    add("upload privacy", "WARN" if public else "PASS",
        "ALLOW_PUBLIC_PUBLISH is ON: uploads may be unlisted/public/scheduled" if public
        else "uploads are forced PRIVATE and unscheduled",
        "Unset ALLOW_PUBLIC_PUBLISH unless public release is intended.")

    active = [name for name, on in notify.channels().items() if on]
    add("notifications", "PASS" if active else "WARN",
        ("channels: " + ", ".join(active)) if active else "no channel configured; alerts go to the console and log only",
        "Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, DISCORD_WEBHOOK_URL or SLACK_WEBHOOK_URL.")

    try:
        free_gb = shutil.disk_usage(str(Path(cfg.OUTPUT_DIR).parent)).free / (1024 ** 3)
        add("disk space", "PASS" if free_gb >= 5 else "WARN", f"{free_gb:.1f} GB free",
            "Free at least 5 GB before rendering a documentary.")
    except OSError:
        pass

    failed = [c for c in checks if c["status"] == "FAIL"]
    warned = [c for c in checks if c["status"] == "WARN"]
    return {"status": "FAIL" if failed else ("WARN" if warned else "PASS"),
            "failed": len(failed), "warnings": len(warned), "checks": checks}


def print_health(report: dict) -> None:
    icons = {"PASS": "OK  ", "WARN": "WARN", "FAIL": "FAIL"}
    for check in report["checks"]:
        print(f"[{icons[check['status']]}] {check['check']}: {check['detail']}")
        if check["status"] != "PASS" and check.get("fix"):
            print(f"         fix: {check['fix']}")
    print(f"\nOverall: {report['status']} ({report['failed']} failed, {report['warnings']} warnings)")
