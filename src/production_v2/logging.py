"""Structured JSON-line logging with credential redaction."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Callable

_SECRET_KEY = re.compile(r"(token|secret|password|api_?key|refresh|authorization)", re.I)


def redact(value: Any) -> Any:
    """Recursively redact anything whose key looks like a credential."""
    if isinstance(value, dict):
        return {k: ("***REDACTED***" if _SECRET_KEY.search(str(k)) else redact(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class JsonLogger:
    def __init__(self, path: Path | None = None, *, run_id: str = "local",
                 sink: Callable[[str], None] | None = None):
        self.path = Path(path) if path else None
        self.run_id = run_id
        self.sink = sink
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, level: str = "INFO", **fields: Any) -> dict:
        record = {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "level": level.upper(),
            "run_id": self.run_id,
            "event": event,
            "fields": redact(fields),
        }
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        if self.path:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        if self.sink:
            self.sink(line)
        return record

    def info(self, event: str, **fields: Any) -> dict:
        return self.log(event, "INFO", **fields)

    def warning(self, event: str, **fields: Any) -> dict:
        return self.log(event, "WARNING", **fields)

    def error(self, event: str, **fields: Any) -> dict:
        return self.log(event, "ERROR", **fields)
