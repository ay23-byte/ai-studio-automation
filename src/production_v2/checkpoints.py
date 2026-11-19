"""Recovery checkpoints so a failed v2 run can resume without re-doing work."""
from __future__ import annotations

from pathlib import Path

from .artifacts import atomic_write_text, read_json


class CheckpointStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = read_json(self.path, {"completed": {}, "failed": {}}) or {"completed": {}, "failed": {}}

    def done(self, stage: str, payload: dict | None = None) -> None:
        self.data.setdefault("completed", {})[stage] = payload or {}
        self.data.get("failed", {}).pop(stage, None)
        self._save()

    def failed(self, stage: str, error: str) -> None:
        self.data.setdefault("failed", {})[stage] = error[:1000]
        self._save()

    def is_done(self, stage: str) -> bool:
        return stage in self.data.get("completed", {})

    def resume_stage(self) -> str | None:
        for stage in ("research", "script", "narration", "visuals", "render", "qa", "publish"):
            if not self.is_done(stage):
                return stage
        return None

    def _save(self) -> None:
        import json
        atomic_write_text(self.path, json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
