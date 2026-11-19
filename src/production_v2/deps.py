"""Dependency preflight checks that never require a paid service."""
from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .config import ROOT, V2Config
from .errors import DependencyError


@dataclass
class DependencyReport:
    status: str
    checks: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"status": self.status, "checks": self.checks}

    def failed(self) -> list[str]:
        return [c["name"] for c in self.checks if c["status"] == "FAIL"]


def _binary(name: str) -> dict:
    path = shutil.which(name)
    return {"name": name, "status": "PASS" if path else "FAIL",
            "detail": path or f"{name} not found on PATH"}


def _module(name: str, *, required: bool = False) -> dict:
    present = importlib.util.find_spec(name) is not None
    status = "PASS" if present else ("FAIL" if required else "WARN")
    return {"name": f"python:{name}", "status": status,
            "detail": "installed" if present else "not installed (optional)"}


def check_dependencies(config: V2Config) -> DependencyReport:
    checks = [_binary(config.ffmpeg_binary)]
    ffprobe = shutil.which(config.ffprobe_binary)
    checks.append({"name": config.ffprobe_binary, "status": "PASS" if ffprobe else "WARN",
                   "detail": ffprobe or "ffprobe absent; ffmpeg -i fallback will be used"})
    checks.extend([
        _module("PIL"),
        _module("requests"),
        _module("numpy"),
        _module("edge_tts"),
    ])
    font = ROOT / "assets" / "fonts" / "NotoSans-Regular.ttf"
    checks.append({"name": "font:NotoSans", "status": "PASS" if font.is_file() else "WARN",
                   "detail": str(font.relative_to(ROOT)) if font.is_file() else "falling back to PIL default font"})
    failed = [c for c in checks if c["status"] == "FAIL"]
    return DependencyReport("FAIL" if failed else "PASS", checks)


def require_dependencies(config: V2Config) -> DependencyReport:
    report = check_dependencies(config)
    if report.status != "PASS":
        raise DependencyError("Required dependencies are missing: " + ", ".join(report.failed()),
                              details=report.as_dict())
    return report
