"""Duplicate prevention for topics, stories and uploads.

Root cause addressed: the scheduled Shorts job rebuilt the same fallback topic
("Compound interest ...") on consecutive days because (a) the fallback list was
compared only with ``topics.json`` titles and never recorded, and (b) the only
durable record of finished work, the SQLite file, is not persisted between
GitHub-hosted runs.

Three independent layers now have to agree before new work starts or uploads:

1. ``state/publish_ledger.json`` — small, human-readable, committed by the
   scheduled workflows next to ``topics.json`` (durable across runners).
2. The local SQLite ``videos`` and ``publish_ledger`` tables (same machine).
3. At upload time, a marker lookup on the authenticated YouTube channel
   (``core.uploader.find_existing_upload``).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from core import config as cfg

LEDGER_VERSION = 1
MARKER_PREFIX = "AI_STUDIO_DEDUPE:"

# Function words and creator filler that do not identify a subject. Includes
# common Roman-Hinglish and Devanagari forms.
_FILLER = {
    "a", "an", "the", "and", "or", "of", "in", "on", "for", "to", "with", "by", "is",
    "are", "was", "were", "be", "how", "what", "why", "when", "where", "who", "which",
    "does", "do", "did", "it", "its", "this", "that", "these", "those", "from", "at",
    "as", "into", "about", "explained", "explain", "explainer", "guide", "complete",
    "simple", "easy", "basics", "introduction", "intro", "vs", "your", "you", "we",
    "kya", "hai", "hain", "hota", "hoti", "hote", "kaise", "kyun", "kyu", "kyon", "ka",
    "ki", "ke", "ko", "se", "mein", "me", "par", "aur", "ya", "yeh", "ye", "woh", "wo",
    "kaam", "karta", "karti", "karte", "kare", "karen", "karein", "samjho", "samjhe",
    "samjhiye", "asan", "aasaan", "aasan", "bhasha", "matlab", "jaane", "jaaniye",
    "jaanein", "liye", "hindi", "hinglish", "english", "shorts", "short", "video",
    "ek", "sab", "kuch", "bhi", "hi", "to", "toh", "na", "nahi", "raha", "rahi", "rahe",
    "क्या", "है", "हैं", "कैसे", "क्यों", "का", "की", "के", "को", "से", "में", "पर",
    "और", "या", "यह", "ये", "वह", "एक", "होता", "होती", "होते", "समझो", "समझिए",
}


def tokens(text) -> set[str]:
    """Significant subject tokens of a title/topic (script-agnostic)."""
    value = unicodedata.normalize("NFKC", str(text or "")).lower()
    words = re.findall(r"[a-z0-9]+|[\u0900-\u097F]+", value)
    result = set()
    for word in words:
        if word in _FILLER or (len(word) < 2 and not word.isdigit()):
            continue
        # Light plural folding so "markets"/"market" match.
        if re.fullmatch(r"[a-z]{4,}s", word) and not word.endswith("ss"):
            word = word[:-1]
        result.add(word)
    return result


def topic_key(text) -> str:
    """Stable identity for a subject, independent of word order and filler."""
    significant = sorted(tokens(text))
    basis = " ".join(significant) if significant else unicodedata.normalize(
        "NFKC", str(text or "")).strip().lower()
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def similarity(left, right) -> float:
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_same_subject(left, right, threshold: float | None = None) -> bool:
    if threshold is None:
        threshold = float(os.getenv("DEDUPE_SIMILARITY_THRESHOLD", "0.6"))
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return False
    if a == b:
        return True
    jaccard = len(a & b) / len(a | b)
    if jaccard >= threshold:
        return True
    smaller = a if len(a) <= len(b) else b
    # A complete, reasonably specific subject contained in a longer title.
    return len(smaller) >= 3 and smaller <= (a & b)


def upload_marker(kind: str, key: str) -> str:
    """Opaque marker appended to a description so uploads are idempotent."""
    return f"{MARKER_PREFIX}{kind}:{key}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    """File-backed record of everything already produced or uploaded."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else cfg.STATE_DIR / "publish_ledger.json"
        self.entries: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            self.entries = []
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Never silently discard a corrupt ledger: an empty ledger would
            # permit duplicate uploads. Fail closed and keep the file.
            raise RuntimeError(
                f"Publish ledger {self.path} is unreadable ({exc}). Repair or restore it; "
                "refusing to continue without duplicate protection."
            ) from exc
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise RuntimeError(f"Publish ledger {self.path} has an unexpected structure.")
        self.entries = [item for item in entries if isinstance(item, dict)]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": LEDGER_VERSION, "updated_at": _now(), "entries": self.entries}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def find(self, kind: str, title: str = "", *, key: str = "", source_refs=()) -> dict | None:
        """Return the existing entry describing the same subject/story, if any."""
        key = key or (topic_key(title) if title else "")
        refs = {str(ref) for ref in (source_refs or []) if str(ref)}
        for entry in self.entries:
            if kind and entry.get("kind") != kind:
                continue
            if key and entry.get("key") == key:
                return entry
            if refs and refs & set(entry.get("source_refs") or []):
                return entry
            for known in (entry.get("title"), entry.get("topic")):
                if title and known and is_same_subject(title, known):
                    return entry
        return None

    def record(self, kind: str, title: str, *, topic: str = "", key: str = "",
               source_refs=(), job_id=None, youtube_id=None, privacy_status=None,
               status: str = "built", extra: dict | None = None) -> dict:
        """Insert or update an entry, save atomically, and mirror it to SQLite."""
        key = key or topic_key(topic or title)
        entry = next((item for item in self.entries
                      if item.get("kind") == kind and item.get("key") == key), None)
        if entry is None:
            entry = {"kind": kind, "key": key, "created_at": _now()}
            self.entries.append(entry)
        entry.update({"title": str(title or entry.get("title") or "")[:300],
                      "status": status, "updated_at": _now()})
        if topic:
            entry["topic"] = str(topic)[:300]
        if source_refs:
            merged = list(dict.fromkeys(list(entry.get("source_refs") or []) +
                                        [str(ref) for ref in source_refs if str(ref)]))
            entry["source_refs"] = merged[:200]
        if job_id is not None:
            entry["job_id"] = job_id
        if youtube_id:
            entry["youtube_id"] = youtube_id
        if privacy_status:
            entry["privacy_status"] = privacy_status
        if extra:
            entry.setdefault("extra", {}).update(extra)
        self.save()
        try:
            from core.db import ledger_put
            ledger_put(kind, key, title=entry["title"],
                       source_ref=",".join((entry.get("source_refs") or [])[:20]),
                       job_id=entry.get("job_id"), youtube_id=entry.get("youtube_id"),
                       privacy_status=entry.get("privacy_status"), status=status,
                       meta=entry.get("extra"))
        except Exception as exc:  # the file ledger is authoritative
            print(f"WARNING: could not mirror ledger entry to SQLite: {exc}")
        return entry


def known_titles_from_db(limit: int = 2000) -> list[dict]:
    """Titles of jobs that already produced a video (or were uploaded)."""
    try:
        from core.db import list_videos
        rows = list_videos(limit=limit)
    except Exception:
        return []
    return [
        {"title": row.get("title") or "", "format": row.get("format") or "",
         "status": row.get("status") or "", "youtube_id": row.get("youtube_id"),
         "job_id": row.get("id")}
        for row in rows
        if row.get("title") and (row.get("youtube_id") or row.get("video_path")
                                 or str(row.get("status") or "") in {"done", "uploaded", "scheduled"})
    ]


def find_duplicate(kind: str, title: str, *, topic: str = "", source_refs=(),
                   ledger: Ledger | None = None, extra_titles=()) -> dict | None:
    """Check every local layer; returns a description of the first match."""
    ledger = ledger or Ledger()
    for candidate in filter(None, (topic, title)):
        hit = ledger.find(kind, candidate, source_refs=source_refs)
        if hit:
            return {"layer": "ledger", "match": hit.get("title") or hit.get("topic"),
                    "youtube_id": hit.get("youtube_id"), "entry": hit}
    fmt_by_kind = {"shorts": {"shorts"}, "longform": {"longform", "long"},
                   "news_short": {"news_short", "shorts"}}
    for row in known_titles_from_db():
        if kind in fmt_by_kind and row["format"] not in fmt_by_kind[kind]:
            continue
        for candidate in filter(None, (topic, title)):
            if is_same_subject(candidate, row["title"]):
                return {"layer": "database", "match": row["title"],
                        "youtube_id": row.get("youtube_id"), "job_id": row.get("job_id")}
    for known in extra_titles or ():
        for candidate in filter(None, (topic, title)):
            if known and is_same_subject(candidate, known):
                return {"layer": "topics", "match": known, "youtube_id": None}
    return None
