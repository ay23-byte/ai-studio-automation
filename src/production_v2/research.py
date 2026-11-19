"""Research dossier validation: sources, claim mapping and evidence gaps."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .models import ResearchDossier, Source, to_dict

STRONG_TIERS = {"primary", "official", "reputable"}
DEMO_TIER = "demo"


@dataclass
class ResearchAssessment:
    accepted: bool
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    bibliography: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return to_dict(self)


def _valid_url(url: str, *, demo_mode: bool) -> bool:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if parsed.hostname and parsed.hostname.endswith(".invalid"):
        return demo_mode
    return True


def _parse_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def validate_source(source: Source, *, demo_mode: bool, today: dt.date) -> list[str]:
    issues: list[str] = []
    if not _valid_url(source.url, demo_mode=demo_mode):
        issues.append(f"{source.id}: unusable source URL")
    if not source.title.strip() or not source.publisher.strip():
        issues.append(f"{source.id}: title and publisher are required")
    published = _parse_date(source.published_at)
    if published is None:
        issues.append(f"{source.id}: publication date is not ISO-8601")
    elif published > today:
        issues.append(f"{source.id}: publication date is in the future")
    allowed = STRONG_TIERS | {DEMO_TIER, "independent", "unverified"}
    if source.reliability not in allowed:
        issues.append(f"{source.id}: unknown reliability tier {source.reliability!r}")
    return issues


def validate_dossier(dossier: ResearchDossier, *, min_sources: int = 2,
                     today: dt.date | None = None) -> ResearchAssessment:
    """Fail closed on unsupported claims; never invent missing evidence."""
    today = today or dt.date.today()
    issues: list[str] = []
    warnings: list[str] = []
    if dossier.rejected_reason:
        return ResearchAssessment(False, [dossier.rejected_reason], [], [])

    if len(dossier.sources) < min_sources and not dossier.demo_mode:
        issues.append(f"only {len(dossier.sources)} sources; at least {min_sources} are required")
    source_ids = {s.id for s in dossier.sources}
    if len(source_ids) != len(dossier.sources):
        issues.append("source IDs must be unique")
    for source in dossier.sources:
        issues.extend(validate_source(source, demo_mode=dossier.demo_mode, today=today))

    claim_ids = {c.id for c in dossier.claims}
    if len(claim_ids) != len(dossier.claims):
        issues.append("claim IDs must be unique")
    strong_sources = [s for s in dossier.sources if s.reliability in STRONG_TIERS]
    if not dossier.demo_mode and not strong_sources:
        issues.append("no primary, official or reputable source is available")

    for claim in dossier.claims:
        if not claim.text.strip():
            issues.append(f"{claim.id}: empty claim")
        if claim.status not in {"established", "interpretation", "allegation", "speculation",
                                "needs_verification"}:
            issues.append(f"{claim.id}: unknown claim status")
        if not claim.source_ids:
            issues.append(f"{claim.id}: no source-to-claim mapping")
        missing = [sid for sid in claim.source_ids if sid not in source_ids]
        if missing:
            issues.append(f"{claim.id}: unknown source IDs {missing}")
        if claim.status == "established" and not claim.demonstration_only:
            if not dossier.demo_mode and not any(
                sid in {s.id for s in strong_sources} for sid in claim.source_ids
            ):
                issues.append(f"{claim.id}: established claim lacks a strong source")
        if claim.status in {"allegation", "speculation"}:
            warnings.append(f"{claim.id}: {claim.status} must not be narrated as confirmed fact")
        if not claim.evidence.strip() and not claim.demonstration_only:
            warnings.append(f"{claim.id}: no excerpt/evidence recorded")

    for disagreement in dossier.disagreements:
        if disagreement.claim_id not in claim_ids:
            issues.append(f"disagreement references unknown claim {disagreement.claim_id}")
        if not disagreement.description.strip():
            issues.append("disagreement has no description")

    bibliography = build_bibliography(dossier)
    return ResearchAssessment(not issues, issues, warnings, bibliography)


def build_bibliography(dossier: ResearchDossier) -> list[str]:
    lines = []
    for index, source in enumerate(dossier.sources, start=1):
        published = _parse_date(source.published_at)
        date_text = published.isoformat() if published else source.published_at
        suffix = " [DEMO SOURCE — NOT PUBLICATION EVIDENCE]" if source.reliability == DEMO_TIER else ""
        lines.append(f"[{index}] {source.publisher}. \"{source.title}\". {date_text}. {source.url}{suffix}")
    return lines


def reject_topic(dossier: ResearchDossier, reason: str) -> ResearchDossier:
    dossier.rejected_reason = reason[:1000]
    return dossier
