"""Operator notifications via optional channels, without third-party HTTP dependencies.

Delivery failures are contained and logged; alerts must not mask pipeline failures.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from core.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

_SECRET = re.compile(
    r"(bot\d+:[A-Za-z0-9_-]+|hooks\.slack\.com/services/\S+|"
    r"discord(?:app)?\.com/api/webhooks/\S+|(?:key|token)=[^&\s]+)", re.I
)


def _safe(text) -> str:
    return _SECRET.sub("[REDACTED]", str(text))


def _post(name, url, attempts=3, **kwargs):
    """POST using stdlib urllib; retry bounded network failures and HTTP 429/5xx."""
    last = ""
    for attempt in range(max(1, attempts)):
        try:
            payload = kwargs.get("json")
            data = kwargs.get("data")
            headers = dict(kwargs.get("headers") or {})
            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
            elif isinstance(data, dict):
                from urllib.parse import urlencode
                data = urlencode(data).encode("utf-8")
                headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=20) as response:
                if 200 <= response.status < 300:
                    return True
                last = f"HTTP {response.status}"
                if response.status != 429 and response.status < 500:
                    break
                delay = min(float(response.headers.get("Retry-After", "2")), 15.0)
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code != 429 and exc.code < 500:
                break
            try:
                delay = min(float(exc.headers.get("Retry-After", "2")), 15.0)
            except (TypeError, ValueError):
                delay = 2.0 * (attempt + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = type(exc).__name__
            delay = 2.0 * (attempt + 1)
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
    """Deliver a message to every configured channel and return per-channel results."""
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
    """Report configured channels without exposing values."""
    return {
        "telegram": bool(os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
                         and os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)),
        "discord": bool(os.getenv("DISCORD_WEBHOOK_URL", "").strip()),
        "slack": bool(os.getenv("SLACK_WEBHOOK_URL", "").strip()),
        "webhook": bool(os.getenv("NOTIFY_WEBHOOK_URL", "").strip()),
        "github_summary": bool(os.getenv("GITHUB_STEP_SUMMARY", "").strip()),
    }
