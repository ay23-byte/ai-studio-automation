"""Topic queue for the scheduled Shorts/long-form pipelines.

Root causes addressed:

* ``topics.json`` was exhausted (all five entries done), after which the cron
  job walked a hard-coded five-item finance list and rebuilt the same subject on
  consecutive days: nothing recorded what had been produced.
* The file was rewritten in place with no lock and no atomic replace, so two
  overlapping runs (or a crash mid-write) could corrupt or lose the queue.

Selection order: approved pending entries in ``topics.json`` → topics proposed
by the language model (deduplicated against everything already made) → the
curated evergreen bank in ``config/topic_bank.json``. Every candidate is checked
against the publish ledger and the job database before any work starts.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import time
from pathlib import Path

from core import config as cfg
from core import dedupe

TOPICS_PATH = cfg.ROOT / "topics.json"
BANK_PATH = cfg.ROOT / "config" / "topic_bank.json"


def _kind(fmt: str) -> str:
    return "longform" if str(fmt).lower() in {"long", "longform"} else "shorts"


@contextlib.contextmanager
def locked(path: Path, timeout: float = 30.0, stale_after: float = 600.0):
    """Cross-platform advisory lock using an exclusive lock file."""
    lock = Path(str(path) + ".lock")
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, str(os.getpid()).encode())
            os.close(descriptor)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale_after:
                    lock.unlink(missing_ok=True)  # crashed holder
                    continue
            except OSError:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"Could not lock {path.name}; another run is updating it.")
            time.sleep(0.2)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def load_topics(path: Path | None = None) -> list:
    path = Path(path or TOPICS_PATH)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeError(f"{path.name} is not valid JSON ({exc}); fix it before the next run.") from exc
    if not isinstance(data, list):
        raise RuntimeError(f"{path.name} must contain a JSON list of topics.")
    return [item for item in data if isinstance(item, dict)]


def save_topics(topics: list, path: Path | None = None) -> None:
    """Atomic replace: a crash can never leave a half-written queue."""
    path = Path(path or TOPICS_PATH)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(topics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def update_topic(match_title: str, path: Path | None = None, **changes) -> bool:
    """Re-read, modify one entry and save under the lock (no lost updates)."""
    path = Path(path or TOPICS_PATH)
    with locked(path):
        topics = load_topics(path)
        hit = False
        for item in topics:
            if (item.get("title") or item.get("topic")) == match_title:
                item.update(changes)
                hit = True
        if hit:
            save_topics(topics, path)
        return hit


def _title_of(item: dict) -> str:
    return str(item.get("title") or item.get("topic") or "").strip()


def _matches_format(item: dict, fmt: str) -> bool:
    declared = str(item.get("format") or "").strip().lower()
    return not declared or _kind(declared) == _kind(fmt)


def next_topic(fmt: str = "shorts", path: Path | None = None, ledger: dedupe.Ledger | None = None):
    """Return ``(topics, topic)``: the first approved pending entry that is not a duplicate.

    Pending entries that duplicate something already produced are marked
    ``skipped`` with the reason instead of being silently rebuilt.
    """
    path = Path(path or TOPICS_PATH)
    ledger = ledger or dedupe.Ledger()
    topics = load_topics(path)
    for item in topics:
        if item.get("done") or item.get("skipped") or item.get("approved") is False:
            continue
        if not _matches_format(item, fmt):
            continue
        title = _title_of(item)
        if not title:
            continue
        duplicate = dedupe.find_duplicate(_kind(fmt), title, topic=str(item.get("topic") or ""),
                                          ledger=ledger)
        if duplicate and not item.get("allow_duplicate"):
            reason = f"duplicate of '{duplicate['match']}' ({duplicate['layer']})"
            print(f"Skipping queued topic '{title}': {reason}.")
            update_topic(title, path, skipped=True, skip_reason=reason)
            continue
        return topics, item
    return topics, None


def covered_titles(fmt: str, ledger: dedupe.Ledger | None = None, limit: int = 150) -> list[str]:
    """Titles already produced, used to steer the model away from repeats."""
    ledger = ledger or dedupe.Ledger()
    titles = [str(entry.get("title") or "") for entry in ledger.entries]
    titles += [row["title"] for row in dedupe.known_titles_from_db()]
    titles += [_title_of(item) for item in load_topics() if item.get("done")]
    return list(dict.fromkeys(title for title in titles if title))[-limit:]


def propose_topics(fmt: str = "shorts", count: int = 8, categories=None, language: str = "english",
                   ledger: dedupe.Ledger | None = None) -> list[dict]:
    """Ask the language model for fresh, non-repeating topics; [] when unavailable."""
    from core.script_engine import _ask_gemini
    ledger = ledger or dedupe.Ledger()
    categories = [c for c in (categories or _default_categories()) if c]
    covered = covered_titles(fmt, ledger)
    prompt = (
        f'You plan content for the educational YouTube channel "{cfg.CHANNEL_NAME}". '
        f"Propose {count} specific, accurate, evergreen topics for a "
        f"{'60-second vertical Short' if _kind(fmt) == 'shorts' else '10-15 minute documentary'}. "
        f"Allowed categories: {', '.join(categories)}. "
        "Each topic must be a single concrete concept or question a curious student would search for, "
        "explainable truthfully without current news, medical or personal financial advice. "
        "Do NOT repeat or rephrase any of these already-covered subjects:\n"
        + "\n".join(f"- {title}" for title in covered[-80:])
        + "\n\nReturn ONLY JSON: {\"topics\":[{\"topic\":\"...\",\"category\":\"...\"}]}. "
          "Write topics in English; the script stage localises them."
    )
    try:
        raw = _ask_gemini(prompt)
        cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip(), flags=re.I)
        rows = json.loads(cleaned).get("topics") or []
    except Exception as exc:
        print(f"Topic proposal unavailable ({type(exc).__name__}); using the curated topic bank.")
        return []
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        topic = str(row.get("topic") or "").strip()
        category = str(row.get("category") or categories[0]).strip().lower()
        if not topic or len(topic) > 140:
            continue
        if category not in categories:
            category = categories[0]
        result.append({"topic": topic, "category": category, "source": "model"})
    return result


def _default_categories() -> list[str]:
    configured = os.getenv("TOPIC_CATEGORIES", "physics,engineering,technology,science")
    return [item.strip().lower() for item in configured.split(",") if item.strip()]


def bank_topics() -> list[dict]:
    try:
        data = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("topics") if isinstance(data, dict) else data
    return [dict(row, source="bank") for row in rows or []
            if isinstance(row, dict) and str(row.get("topic") or "").strip()]


def pick_fresh_topic(fmt: str = "shorts", language: str = "english", use_model: bool = True,
                     ledger: dedupe.Ledger | None = None) -> dict | None:
    """First non-duplicate topic from the model's proposals, then the curated bank."""
    ledger = ledger or dedupe.Ledger()
    queued = [_title_of(item) for item in load_topics()]
    candidates = (propose_topics(fmt, language=language, ledger=ledger) if use_model else []) + bank_topics()
    for candidate in candidates:
        duplicate = dedupe.find_duplicate(_kind(fmt), candidate["topic"], ledger=ledger,
                                          extra_titles=queued)
        if duplicate:
            continue
        return candidate
    return None


def remaining(fmt: str | None = None) -> int:
    return sum(1 for item in load_topics()
               if not item.get("done") and not item.get("skipped") and item.get("approved") is not False
               and (fmt is None or _matches_format(item, fmt)))
