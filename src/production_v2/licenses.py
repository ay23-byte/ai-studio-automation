"""Asset rights records, licence screening and per-production credits."""
from __future__ import annotations

import csv
import datetime as dt
from dataclasses import asdict, dataclass
from pathlib import Path

ALLOWED = (
    "cc0", "public domain", "public-domain", "cc by", "cc by-sa", "cc-by", "cc-by-sa",
    "nasa media usage guidelines", "us government work", "loc no known restrictions",
    "mit", "apache-2.0", "ofl",
)
BLOCKED = ("all rights reserved", "proprietary", "cc by-nc", "cc-by-nc", "cc by-nd", "cc-by-nd")


@dataclass
class AssetRecord:
    id: str
    source_url: str
    creator: str
    license: str
    attribution_text: str = ""
    downloaded_at: str = ""
    sha256: str = ""
    media_type: str = "image"
    resolution: str = ""
    duration_seconds: float = 0.0
    restrictions: str = ""
    used_in: str = ""
    synthetic: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class RightsDecision:
    eligible: bool
    status: str  # eligible|incomplete|blocked
    reasons: list[str]


def validate_asset(record: AssetRecord) -> RightsDecision:
    license_text = (record.license or "").strip().lower()
    reasons: list[str] = []
    if any(term in license_text for term in BLOCKED):
        return RightsDecision(False, "blocked", ["licence forbids this use or derivatives"])
    if record.synthetic:
        return RightsDecision(True, "eligible", ["synthetic graphic created for this production"])
    if not license_text:
        return RightsDecision(False, "incomplete", ["licence label is missing"])
    if not any(term in license_text for term in ALLOWED):
        return RightsDecision(False, "blocked", [f"unrecognised licence: {record.license}"])
    if not record.source_url.startswith(("http://", "https://")):
        reasons.append("source URL is missing or invalid")
    if (license_text.startswith("cc by") or "cc-by" in license_text) and not record.attribution_text.strip():
        reasons.append("attribution text is required for this licence")
    if not record.sha256:
        reasons.append("file checksum is not recorded")
    status = "eligible" if not reasons else "incomplete"
    return RightsDecision(not reasons, status, reasons or ["licence terms recorded"])


class AssetLibrary:
    def __init__(self):
        self.records: dict[str, AssetRecord] = {}

    def add(self, record: AssetRecord) -> RightsDecision:
        self.records[record.id] = record
        return validate_asset(record)

    def decisions(self) -> list[dict]:
        return [{"asset": r.id, **validate_asset(r).__dict__} for r in self.records.values()]

    def all_eligible(self) -> bool:
        return bool(self.records) and all(validate_asset(r).eligible for r in self.records.values())

    def write_licenses_csv(self, path: Path) -> Path:
        path = Path(path)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["asset_id", "source_url", "creator", "license", "attribution",
                             "downloaded_at", "sha256", "media_type", "resolution",
                             "duration_seconds", "restrictions", "used_in", "rights_status"])
            for record in self.records.values():
                decision = validate_asset(record)
                writer.writerow([record.id, record.source_url, record.creator, record.license,
                                 record.attribution_text, record.downloaded_at or dt.date.today().isoformat(),
                                 record.sha256, record.media_type, record.resolution,
                                 record.duration_seconds, record.restrictions, record.used_in,
                                 decision.status])
        return path

    def write_credits(self, path: Path) -> Path:
        lines = ["Source credits and rights notes", "=============================", ""]
        required = [r for r in self.records.values()
                    if r.license.lower().startswith("cc by") or "cc-by" in r.license.lower()]
        if required:
            lines.append("Attribution required:")
            for record in required:
                lines.append(f"- {record.attribution_text or record.creator} — {record.source_url} ({record.license})")
            lines.append("")
        lines.append("All assets used in this production:")
        for record in self.records.values():
            lines.append(f"- {record.id}: {record.creator}, {record.license}, {record.source_url}")
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return Path(path)
