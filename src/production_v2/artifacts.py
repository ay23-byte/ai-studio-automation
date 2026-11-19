"""Per-run artifact storage, checksums and integrity manifests."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from .errors import ValidationError


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp", delete=False) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def validate_file(path: Path, *, min_bytes: int = 1, label: str = "artifact") -> dict:
    path = Path(path)
    if not path.is_file():
        raise ValidationError(f"{label} does not exist: {path}")
    size = path.stat().st_size
    if size < min_bytes:
        raise ValidationError(f"{label} is smaller than {min_bytes} bytes: {size}")
    return {"path": str(path), "size_bytes": size, "sha256": sha256_file(path)}


class ArtifactStore:
    """Creates deterministic paths inside an isolated run directory."""

    def __init__(self, root: Path, run_id: str):
        self.run_id = run_id
        self.root = Path(root) / run_id
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.root / name

    def write_json(self, name: str, payload: Any) -> Path:
        target = self.path(name)
        write_json(target, payload)
        return target

    def write_text(self, name: str, text: str) -> Path:
        target = self.path(name)
        atomic_write_text(target, text)
        return target

    def write_csv(self, name: str, rows: list[dict], headers: list[str]) -> Path:
        target = self.path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return target

    def build_manifest(self) -> dict:
        files = []
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and path.name != "artifact_manifest.json":
                files.append({"path": str(path.relative_to(self.root)),
                              "size_bytes": path.stat().st_size,
                              "sha256": sha256_file(path)})
        return {"run_id": self.run_id, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "files": files}

    def finalize_manifest(self) -> Path:
        return self.write_json("artifact_manifest.json", self.build_manifest())
