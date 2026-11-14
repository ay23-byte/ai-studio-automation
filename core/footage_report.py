"""Per-run footage diagnostics: ``provider_report.json`` and a readable summary.

Before this module every provider failure was a ``print`` line that scrolled past
in a build log. A documentary that fell back to one provider for every scene left
no record of whether the others were unconfigured, refused the key, were rate
limited, returned nothing relevant, or returned files that failed validation.

The report records what happened; it never upgrades a result. A provider is
"working" here only when a downloaded file passed validation.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

REPORT_JSON = "provider_report.json"
REPORT_MARKDOWN = "provider_report.md"
SCHEMA_VERSION = 1

# Query-string values that must never reach a log, report or artifact.
_SECRET_PARAMS = ("api_key", "apikey", "key", "wskey", "token", "access_token", "client_secret", "signature")
_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:" + "|".join(_SECRET_PARAMS) + r")=)[^&\s\"']+")


def redact(value: object) -> str:
    """Remove credentials from a URL or message before it is stored or printed."""
    text = str(value if value is not None else "")
    text = _SECRET_PATTERN.sub(r"\1REDACTED", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}", r"\1REDACTED", text)
    return text


def _new_provider_entry(name: str) -> dict:
    return {
        "provider": name,
        "enabled": None,            # True / False once the provider has been asked
        "auth_status": "unknown",   # not_required | configured | missing_key | rejected | unknown
        "search_status": "not_attempted",
        "http_status": None,        # last HTTP status seen
        "http_status_counts": {},
        "searches": 0,
        "results_found": 0,
        "downloadable_candidates": 0,
        "download_attempts": 0,
        "download_successes": 0,
        "invalid_media": 0,
        "licence_rejections": 0,
        "rejection_reasons": {},    # why results were not offered, with counts
        "eligible_assets": 0,
        "selected_scenes": 0,
        "retries": 0,
        "failure_reason": "",
        "disabled_reason": "",
    }


class ProviderReport:
    """Thread-safe counters for one documentary (or one verification) run."""

    def __init__(self, label: str = ""):
        self.label = label
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.providers: dict[str, dict] = {}
        self.scenes: list[dict] = []
        self.notes: list[str] = []
        self._lock = threading.RLock()

    # -- provider counters -------------------------------------------------
    def provider(self, name: str) -> dict:
        with self._lock:
            return self.providers.setdefault(name, _new_provider_entry(name))

    def set(self, name: str, **fields):
        with self._lock:
            self.provider(name).update({key: (redact(value) if isinstance(value, str) else value)
                                        for key, value in fields.items()})

    def add(self, name: str, field: str, amount: int = 1):
        with self._lock:
            entry = self.provider(name)
            entry[field] = int(entry.get(field) or 0) + amount

    def reject(self, name: str, reason: str, *, licence: bool = True, amount: int = 1):
        """Count a result that was not offered, under a short reason a person can act on."""
        with self._lock:
            entry = self.provider(name)
            reasons = entry.setdefault("rejection_reasons", {})
            key = redact(str(reason))[:140]
            reasons[key] = int(reasons.get(key) or 0) + amount
            if licence:
                entry["licence_rejections"] = int(entry.get("licence_rejections") or 0) + amount

    def http_status(self, name: str, status: int):
        with self._lock:
            entry = self.provider(name)
            entry["http_status"] = int(status)
            counts = entry["http_status_counts"]
            counts[str(status)] = counts.get(str(status), 0) + 1
            if status in (401, 403):
                entry["auth_status"] = "rejected"
            elif status == 200 and entry["auth_status"] in ("unknown", "configured"):
                entry["auth_status"] = "configured" if entry["auth_status"] == "configured" else entry["auth_status"]

    def auth_recovered(self, name: str, note: str):
        """A refused request succeeded in another documented form; keep the note, not the failure."""
        with self._lock:
            entry = self.provider(name)
            entry["auth_status"] = "configured"
            entry["auth_note"] = redact(note)[:400]
            entry["failure_reason"] = ""
            if entry["search_status"] == "auth_rejected":
                entry["search_status"] = "not_attempted"

    def fail(self, name: str, reason: str, *, search_status: str | None = None):
        """Record the most recent failure reason; an earlier success is not erased."""
        with self._lock:
            entry = self.provider(name)
            entry["failure_reason"] = redact(reason)[:400]
            if search_status and entry["search_status"] != "ok":
                entry["search_status"] = search_status

    # -- scenes ------------------------------------------------------------
    def scene(self, outcome: dict):
        with self._lock:
            self.scenes.append(json.loads(redact(json.dumps(outcome, ensure_ascii=False, default=str))))

    def note(self, text: str):
        with self._lock:
            self.notes.append(redact(text))

    # -- output ------------------------------------------------------------
    def to_dict(self) -> dict:
        with self._lock:
            providers = [dict(entry) for entry in self.providers.values()]
            scenes = list(self.scenes)
            notes = list(self.notes)
        selected = [scene for scene in scenes if scene.get("status") == "selected"]
        manual = [scene for scene in scenes if scene.get("status") == "needs_manual_review"]
        share: dict[str, int] = {}
        for scene in selected:
            name = str((scene.get("selected") or {}).get("provider") or "unknown")
            share[name] = share.get(name, 0) + 1
        return {
            "schema_version": SCHEMA_VERSION,
            "label": self.label,
            "started_at": self.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "summary": {
                "scenes_searched": len(scenes),
                "scenes_with_footage": len(selected),
                "scenes_needing_manual_review": len(manual),
                "providers_with_validated_download": sorted(
                    entry["provider"] for entry in providers if entry["download_successes"]),
                "selected_by_provider": dict(sorted(share.items(), key=lambda item: (-item[1], item[0]))),
            },
            "providers": sorted(providers, key=lambda entry: entry["provider"].lower()),
            "scenes": scenes,
            "manual_review_scenes": [
                {"scene": scene.get("scene"), "reason": scene.get("reason"), "queries": scene.get("queries")}
                for scene in manual],
            "notes": notes,
            "definitions": {
                "eligible_assets": "downloaded files that passed ffprobe validation and the licence policy",
                "downloadable_candidates": "search results with a direct media URL and a publishable licence state",
                "auth_status": "configured means a key is present; rejected means the provider answered 401/403",
                "important": "a result counts as working footage only after its downloaded file passed validation",
            },
        }

    def write(self, build_dir) -> dict:
        root = Path(build_dir)
        root.mkdir(parents=True, exist_ok=True)
        data = self.to_dict()
        json_path = root / REPORT_JSON
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path = root / REPORT_MARKDOWN
        markdown_path.write_text(render_markdown(data), encoding="utf-8")
        return {"json_path": str(json_path), "markdown_path": str(markdown_path), "summary": data["summary"]}


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "/").replace("\n", " ").strip() or "-"


def render_markdown(data: dict) -> str:
    """Human-readable summary of a provider report."""
    summary = data.get("summary") or {}
    lines = [
        "# Footage provider report",
        "",
        f"Run: {data.get('label') or 'unnamed'}  ",
        f"Started: {data.get('started_at')}  Finished: {data.get('finished_at')}",
        "",
        f"- Scenes searched: {summary.get('scenes_searched', 0)}",
        f"- Scenes with validated footage: {summary.get('scenes_with_footage', 0)}",
        f"- Scenes needing manual review: {summary.get('scenes_needing_manual_review', 0)}",
        "- Providers with at least one validated download: "
        + (", ".join(summary.get("providers_with_validated_download") or []) or "none"),
        "",
        "A provider counts as working only when a downloaded file passed validation. "
        "A successful search alone proves nothing about the footage.",
        "",
        "## Providers",
        "",
        "| Provider | Enabled | Auth | Search | HTTP | Results | Downloadable | Downloads ok | Invalid media "
        "| Licence rejections | Eligible | Selected | Retries | Failure reason |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for entry in data.get("providers") or []:
        enabled = {True: "yes", False: "no"}.get(entry.get("enabled"), "not asked")
        lines.append("| " + " | ".join(_cell(value) for value in (
            entry.get("provider"), enabled, entry.get("auth_status"), entry.get("search_status"),
            entry.get("http_status"), entry.get("results_found"), entry.get("downloadable_candidates"),
            f"{entry.get('download_successes')}/{entry.get('download_attempts')}", entry.get("invalid_media"),
            entry.get("licence_rejections"), entry.get("eligible_assets"), entry.get("selected_scenes"),
            entry.get("retries"), entry.get("failure_reason") or entry.get("disabled_reason"),
        )) + " |")
    refusals = [(entry.get("provider"), entry.get("rejection_reasons") or {}) for entry in data.get("providers") or []]
    if any(reasons for _name, reasons in refusals):
        lines += ["", "## Why results were not offered", ""]
        for name, reasons in refusals:
            for reason, count in sorted(reasons.items(), key=lambda item: -item[1]):
                lines.append(f"- {_cell(name)}: {count} × {_cell(reason)}")
    notes = [(entry.get("provider"), entry.get("auth_note")) for entry in data.get("providers") or [] if entry.get("auth_note")]
    for name, note in notes:
        lines += ["", f"Note for {_cell(name)}: {_cell(note)}"]
    share = summary.get("selected_by_provider") or {}
    if share:
        lines += ["", "## Footage selected, by provider", ""]
        total = sum(share.values()) or 1
        for name, count in share.items():
            lines.append(f"- {name}: {count} scene(s), {100 * count / total:.0f}%")
    scenes = data.get("scenes") or []
    if scenes:
        lines += ["", "## Scenes", "",
                  "| Scene | Outcome | Provider | Asset | Licence state | Relevance | Size | Why |",
                  "|---|---|---|---|---|---|---|---|"]
        for scene in scenes:
            chosen = scene.get("selected") or {}
            size = f"{chosen.get('width')}x{chosen.get('height')}" if chosen.get("width") else "-"
            lines.append("| " + " | ".join(_cell(value) for value in (
                scene.get("scene"), scene.get("status"), chosen.get("provider"),
                str(chosen.get("title") or chosen.get("asset_id") or "")[:60], chosen.get("licence_state"),
                chosen.get("relevance_score"), size, str(scene.get("reason") or "")[:160],
            )) + " |")
    manual = data.get("manual_review_scenes") or []
    if manual:
        lines += ["", "## Scenes that need a person", "",
                  "No provider returned relevant, licensed, valid footage for these scenes. "
                  "Nothing unrelated was substituted."]
        for item in manual:
            lines.append(f"- {item.get('scene')}: {item.get('reason')}")
    if data.get("notes"):
        lines += ["", "## Notes", ""] + [f"- {note}" for note in data["notes"]]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Current run and current provider. The acquisition functions keep their
# historical signatures, so the active report travels through module state.
# ---------------------------------------------------------------------------
_STATE = {"report": None}
_LOCAL = threading.local()


def begin_run(label: str = "") -> ProviderReport:
    report = ProviderReport(label)
    _STATE["report"] = report
    return report


def current_report() -> ProviderReport:
    if _STATE["report"] is None:
        _STATE["report"] = ProviderReport("implicit run")
    return _STATE["report"]


def end_run():
    _STATE["report"] = None


class provider_scope:
    """Mark which provider the current thread is calling, for HTTP diagnostics."""

    def __init__(self, name: str, report: ProviderReport | None = None):
        self.name, self.report = name, report

    def __enter__(self):
        self.previous = getattr(_LOCAL, "scope", None)
        _LOCAL.scope = (self.name, self.report or current_report())
        return self

    def __exit__(self, *_exc):
        _LOCAL.scope = self.previous
        return False


def active_scope():
    """``(provider name, report)`` for this thread, or ``(None, None)``."""
    return getattr(_LOCAL, "scope", None) or (None, None)
