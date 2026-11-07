"""Deterministic source-tier and independence gate for human-reviewed news packets.

This module validates provenance metadata; it does not browse, fact-check, or
prove that an article supports a claim. A human must inspect each cited passage.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
TRUSTED_PATH = ROOT / "config" / "trusted_sources.json"
BLOCK_PATH = ROOT / "config" / "blocklist_sources.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _host(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return parsed.hostname.lower().removeprefix("www.").rstrip(".")


def _matches(host: str, domains: list[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def classify_source(source: dict, trusted: dict | None = None,
                    blocklist: dict | None = None) -> str:
    trusted = trusted or _load(TRUSTED_PATH)
    blocklist = blocklist or _load(BLOCK_PATH)
    host = _host(source.get("url", ""))
    if not host:
        return "unknown"
    if _matches(host, blocklist.get("domains", [])):
        return "blocked"
    if any(k.lower() in host for k in blocklist.get("keywords_in_domain", [])):
        return "blocked"
    for key, tier in (("tier1_primary", "tier1"), ("tier2_major", "tier2"),
                      ("tier3_specialist", "tier3"), ("social_lead_only", "social")):
        if _matches(host, trusted.get(key, [])):
            return tier
    # Official primary sources may be explicitly reviewed and added to tier1;
    # do not infer tier1 from a URL, badge, or source name.
    return "unknown"


def _parse_date(value: str):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _wire_origin(source: dict, trusted: dict) -> str:
    text = " ".join(str(source.get(k, "")) for k in
                    ("origin", "wire", "text_snippet", "title")).lower()
    for marker in trusted.get("wire_markers", []):
        if marker.lower() in text:
            return marker.lower()
    return "own:" + _host(source.get("url", ""))


def verify_fact_sources(fact: dict, sources: list[dict], *,
                        breaking: bool = True, now: datetime | None = None,
                        trusted: dict | None = None,
                        blocklist: dict | None = None) -> dict:
    """Return a transparent deterministic result for one fact's source_ids."""
    trusted = trusted or _load(TRUSTED_PATH)
    blocklist = blocklist or _load(BLOCK_PATH)
    by_id = {str(s.get("source_id")): s for s in sources if s.get("source_id")}
    ids = fact.get("source_ids", [])
    if not isinstance(ids, list) or not ids:
        return {"ok": False, "reason": "fact has no source_ids", "sources": []}
    now = now or datetime.now(timezone.utc)
    max_age = timedelta(hours=trusted.get("freshness_hours", {}).get(
        "breaking" if breaking else "explainer", 48 if breaking else 168))
    accepted = []
    rejected = []
    for source_id in ids:
        source = by_id.get(str(source_id))
        if not source:
            rejected.append({"source_id": source_id, "reason": "unknown source_id"})
            continue
        tier = classify_source(source, trusted, blocklist)
        if tier in {"blocked", "unknown", "social"}:
            rejected.append({"source_id": source_id, "tier": tier, "reason": "not eligible to confirm"})
            continue
        published = _parse_date(source.get("published", ""))
        if tier != "tier1" and (published is None or now - published > max_age or published > now + timedelta(hours=1)):
            rejected.append({"source_id": source_id, "tier": tier, "reason": "missing, stale, or future publication date"})
            continue
        accepted.append({"source_id": str(source_id), "tier": tier,
                         "origin": _wire_origin(source, trusted),
                         "domain": _host(source.get("url", ""))})
    origins = {s["origin"] for s in accepted}
    has_t1 = any(s["tier"] == "tier1" for s in accepted)
    has_t2 = any(s["tier"] == "tier2" for s in accepted)
    has_t3 = any(s["tier"] == "tier3" for s in accepted)
    enough = has_t1 or (has_t2 and len(origins) >= 2)
    reason = "tier1 primary source" if has_t1 else (
        "two independent eligible sources including tier2" if enough else
        "not enough independent eligible sources; require tier1 or two independent sources including tier2")
    return {"ok": enough, "reason": reason, "sources": accepted, "rejected": rejected}


def verify_packet(packet: dict, *, breaking: bool = True,
                  now: datetime | None = None) -> dict:
    """Verify each claimed confirmed fact and fail closed if any fact fails."""
    sources = packet.get("sources", [])
    facts = packet.get("verified_facts", [])
    results = []
    for index, fact in enumerate(facts, 1):
        result = verify_fact_sources(fact, sources, breaking=breaking, now=now)
        results.append({"fact_index": index, "fact": fact.get("fact", ""),
                        **result})
    failures = [r for r in results if not r["ok"]]
    return {"safe_to_draft": bool(facts) and not failures,
            "fact_checks": results, "failed_fact_count": len(failures),
            "notice": "Metadata checks do not establish that a source supports a fact; human passage review remains mandatory."}
