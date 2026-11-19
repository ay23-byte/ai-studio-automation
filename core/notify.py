"""Operator notifications with requests optional and a stdlib fallback."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import requests
except ImportError:  # keep alerts working during failed dependency installs
    requests = None

from core.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

_SECRET = re.compile(
    r"(bot\d+:[A-Za-z0-9_-]+|hooks\.slack\.com/services/\S+|"
    r"discord(?:app)?\.com/api/webhooks/\S+|(?:key|token)=[^&\s]+)", re.I
)


def _safe(text) -> str:
    return _SECRET.sub("[REDACTED]", str(text))


def _stdlib_post(url, *, data=None, json_body=None, timeout=20):
    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
    elif data is not None:
        payload = urlencode(data).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    else:
        payload, headers = b"", {}
    request = Request(url, data=payload, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers)
    except HTTPError as exc:
        return exc.code, dict(exc.headers or {})
    except URLError as exc:
        raise OSError(type(exc.reason).__name__) from exc


def _post(name, url, attempts=3, **kwargs):
    """POST with bounded retry on network errors, 429 and 5xx."""
    last = ""
    for attempt in range(max(1, attempts)):
        delay = 2.0 * (attempt + 1)
        try:
            if requests is not None:
                response = requests.post(url, timeout=20, **kwargs)
                status, headers = response.status_code, response.headers
            else:
                status, headers = _stdlib_post(
                    url, timeout=20, data=kwargs.get("data"), json_body=kwargs.get("json")
                )
            if status < 300:
                return True
            last = f"HTTP {status}"
            if status != 429 and status < 500:
                break
            try:
                delay = min(float(headers.get("Retry-After", "")), 15.0)
            except (TypeError, ValueError):
                pass
        except Exception as exc:
            last = type(exc).__name__
        if attempt < attempts - 1:
            time.sleep(delay)
    print(f"{name} notification failed: {_safe(last)}")
    return False


def _log_dir() -> Path:
    from core import config as cfg
    return Path(getattr(cfg, "LOG_DIR", cfg.OUTPUT_DIR / "logs"))


def _record(msg, level, delivered):
    try:
        directory = _log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "notifications.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "level": level, "message": msg[:3500], "delivered": delivered,
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _level(msg: str) -> str:
    if msg.startswith("❌"):
        return "error"
    if msg.startswith("⚠"):
        return "warning"
    return "info"


def send(msg: str, level: str | None = None) -> dict:
    """Deliver msg to every configured channel; returns per-channel results."""
    msg = str(msg)
    level = level or _level(msg)
    print("NOTIFY:", msg[:200])
    delivered = {}
    token = os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
    chat = os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
    if token and chat:
        delivered["telegram"] = _post(
            "Telegram", f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": msg[:3500], "disable_web_page_preview": "true"})
    discord = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if discord:
        delivered["discord"] = _post("Discord", discord, json={"content": msg[:1900]})
    slack = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if slack:
        delivered["slack"] = _post("Slack", slack, json={"text": msg[:3500]})
    webhook = os.getenv("NOTIFY_WEBHOOK_URL", "").strip()
    if webhook:
        delivered["webhook"] = _post("Webhook", webhook, json={
            "text": msg[:3500], "level": level, "source": "ai-studio-automation",
            "run_url": _run_url()})
    summary = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(f"\n- **{level.upper()}** {msg[:1500]}\n")
            delivered["github_summary"] = True
        except OSError:
            delivered["github_summary"] = False
    _record(msg, level, delivered)
    return delivered


def _run_url() -> str:
    server, repo, run_id = (os.getenv("GITHUB_SERVER_URL"), os.getenv("GITHUB_REPOSITORY"),
                            os.getenv("GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else ""


def channels() -> dict:
    """Which channels are configured (presence only, never values)."""
    return {
        "telegram": bool(os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
                         and os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)),
        "discord": bool(os.getenv("DISCORD_WEBHOOK_URL", "").strip()),
        "slack": bool(os.getenv("SLACK_WEBHOOK_URL", "").strip()),
        "webhook": bool(os.getenv("NOTIFY_WEBHOOK_URL", "").strip()),
        "github_summary": bool(os.getenv("GITHUB_STEP_SUMMARY", "").strip()),
    }
