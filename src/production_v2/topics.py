"""Topic and story history with conservative deduplication."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from .artifacts import read_json, write_json

STOPWORDS = {"the", "a", "an", "of", "and", "or", "in", "on", "for", "to", "with", "is", "are", "why", "how"}


def normalize_title(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", str(title).lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def similarity(left: str, right: str) -> float:
    a, b = normalize_title(left), normalize_title(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class TopicEntry:
    key: str
    title: str
    content_type: str
    status: str
    recorded_at: str
    youtube_id: str = ""


class TopicHistory:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.entries: list[dict] = read_json(self.path, []) or []

    def record(self, title: str, content_type: str, status: str = "PLANNED",
               youtube_id: str = "") -> TopicEntry:
        entry = TopicEntry(
            key=re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"),
            title=title, content_type=content_type, status=status,
            recorded_at=dt.datetime.now(dt.timezone.utc).isoformat(), youtube_id=youtube_id)
        self.entries.append(entry.__dict__)
        self._save()
        return entry

    def is_duplicate(self, title: str, content_type: str, *, threshold: float = 0.72) -> bool:
        return any(
            e.get("content_type") == content_type
            and (similarity(title, e.get("title", "")) >= threshold
                 or (e.get("key") and e["key"] == re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")))
            for e in self.entries
        )

    def suitable_topic(self, candidates: list[dict], content_type: str) -> dict | None:
        """Pick the first candidate with evidence that is not a near repeat."""
        for candidate in candidates:
            if not candidate.get("research_available", True):
                continue
            if self.is_duplicate(candidate.get("title", ""), content_type):
                continue
            return candidate
        return None

    def _save(self) -> None:
        write_json(self.path, self.entries)
