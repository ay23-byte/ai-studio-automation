"""Claim-ledger and source-metadata verification for long-form documentaries.

Automated checks validate traceability metadata only. They never certify a claim
as true; an editor must open sources and record an evidence-based decision.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCHEMA_VERSION = 1
CLAIM_STATES = {"pending", "verified", "disputed", "unsupported"}
SOURCE_STATES = {"pending", "reviewed", "needs_correction"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normal_url(value: str) -> str:
    try:
        parsed = urlparse(str(value or "").strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return ""
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
    except Exception:
        return ""


def _fingerprint(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_research_verification_report(script: dict[str, Any],
                                       discovery_sources: list[dict] | None = None) -> dict[str, Any]:
    """Build source and claim traceability without promoting model output to verified fact."""
    discovery = {_normal_url(row.get("url")) for row in (discovery_sources or []) if _normal_url(row.get("url"))}
    raw_sources = script.get("sources") or []
    source_rows, source_ids, issues = [], set(), []
    for index, raw in enumerate(raw_sources):
        if not isinstance(raw, dict):
            issues.append({"level": "error", "message": f"Source record {index + 1} is not an object."})
            continue
        source_id = str(raw.get("source_id") or raw.get("id") or f"S{index + 1}").strip()
        if not source_id or source_id in source_ids:
            issues.append({"level": "error", "message": f"Source record {index + 1} has a missing or duplicate source ID."})
            continue
        source_ids.add(source_id)
        url = str(raw.get("url") or "").strip()
        normalized = _normal_url(url)
        valid = bool(normalized)
        in_discovery = valid and normalized in discovery
        if not valid:
            issues.append({"level": "warning", "message": f"Source {source_id} has an invalid or missing HTTP(S) URL.", "source_id": source_id})
        if not raw.get("title") or not raw.get("publisher"):
            issues.append({"level": "warning", "message": f"Source {source_id} is missing a title or publisher.", "source_id": source_id})
        published = str(raw.get("published_at") or "").strip()
        if not published or published.lower() in {"unknown", "n/a", "not supplied"}:
            issues.append({"level": "info", "message": f"Source {source_id} has no confirmed publication date.", "source_id": source_id})
        if valid and not in_discovery:
            issues.append({"level": "warning", "message": f"Source {source_id} was not among retrieved discovery leads; independently inspect the URL and publisher.", "source_id": source_id})
        row = {
            "source_id": source_id,
            "title": str(raw.get("title") or "")[:500],
            "publisher": str(raw.get("publisher") or "")[:240],
            "published_at": published or "unknown",
            "url": url,
            "url_valid": valid,
            "metadata_complete": bool(raw.get("title") and raw.get("publisher")),
            "matched_discovery_lead": bool(in_discovery),
            "supports": [str(item)[:800] for item in (raw.get("supports") or [])[:30]],
            "verification_state": "discovery_lead_only" if in_discovery else ("requires_manual_source_validation" if valid else "invalid_url"),
            "fingerprint": _fingerprint({
                "title": raw.get("title"), "publisher": raw.get("publisher"),
                "published_at": published, "url": url,
            }),
        }
        source_rows.append(row)

    raw_claims = script.get("claims") or []
    claim_rows = []
    claim_ids = set()
    for index, raw in enumerate(raw_claims):
        if not isinstance(raw, dict):
            issues.append({"level": "error", "message": f"Claim record {index + 1} is not an object."})
            continue
        claim_id = str(raw.get("claim_id") or raw.get("id") or f"C{index + 1}").strip()
        if not claim_id or claim_id in claim_ids:
            issues.append({"level": "error", "message": f"Claim record {index + 1} has a missing or duplicate claim ID."})
            continue
        claim_ids.add(claim_id)
        text = " ".join(str(raw.get("text") or raw.get("claim") or "").split())
        raw_refs = raw.get("source_ids") or []
        if isinstance(raw_refs, str):
            raw_refs = [raw_refs]
        refs = [str(item) for item in raw_refs]
        evidence = " ".join(str(raw.get("evidence") or "").split())
        valid_refs = [ref for ref in refs if ref in source_ids]
        missing_refs = [ref for ref in refs if ref not in source_ids]
        supported = bool(text and valid_refs and evidence and not missing_refs)
        if not text:
            issues.append({"level": "warning", "message": f"Claim {claim_id} has no claim text.", "claim_id": claim_id})
        if not refs or missing_refs:
            issues.append({"level": "warning", "message": f"Claim {claim_id} has missing or unknown source references.", "claim_id": claim_id, "unknown_source_ids": missing_refs})
        if not evidence:
            issues.append({"level": "warning", "message": f"Claim {claim_id} has no evidence note.", "claim_id": claim_id})
        row = {
            "claim_id": claim_id, "text": text,
            "claim_type": str(raw.get("claim_type") or "factual"),
            "source_ids": refs, "valid_source_ids": valid_refs,
            "evidence": evidence,
            "traceability_status": "source_linked_pending_human_review" if supported else "needs_verification",
            "fingerprint": _fingerprint({"text": text, "claim_type": raw.get("claim_type"),
                                         "source_ids": refs, "evidence": evidence}),
        }
        claim_rows.append(row)

    if not source_rows:
        issues.append({"level": "warning", "message": "No source records were supplied."})
    if not claim_rows:
        issues.append({"level": "error", "message": "No structured claim ledger was supplied; regenerate the script with claim-level source references."})
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": _now(),
        "source_count": len(source_rows),
        "discovery_lead_count": sum(row["matched_discovery_lead"] for row in source_rows),
        "claim_count": len(claim_rows),
        "sources": source_rows,
        "claims": claim_rows,
        "fact_check_complete": False,
        "coverage_review_required": True,
        "issues": issues,
        "notice": "URLs and model-supplied evidence are traceability leads only. They do not establish that a claim is true or that an article contains the quoted evidence. A human must open each source, inspect the relevant passage/data, check counter-evidence and confirm claim-ledger completeness.",
    }


def _decision_path(build_dir: str | Path) -> Path:
    return Path(build_dir) / "research_review_decisions.json"


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def load_research_reviews(build_dir: str | Path, report: dict[str, Any]) -> dict[str, Any]:
    path = _decision_path(build_dir)
    if path.is_file():
        try:
            store = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot read research review decisions {path}: {exc}") from exc
        if not isinstance(store, dict) or store.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Unsupported research review schema in {path}.")
    else:
        store = {"schema_version": SCHEMA_VERSION, "updated_at": None,
                 "coverage_reviewed": False, "coverage_notes": "", "sources": {}, "claims": {}}
    if not isinstance(store.get("sources"), dict) or not isinstance(store.get("claims"), dict):
        raise ValueError(f"Research review decisions {path} must contain source and claim objects.")
    if not isinstance(store.get("coverage_reviewed", False), bool):
        raise ValueError(f"Research review decisions {path} has an invalid coverage_reviewed value.")
    for item_id, decision in store["sources"].items():
        if not isinstance(decision, dict) or decision.get("status", "pending") not in SOURCE_STATES:
            raise ValueError(f"Research source decision {item_id!r} is malformed.")
        if not isinstance(decision.get("reviewer_notes", ""), str):
            raise ValueError(f"Research source notes for {item_id!r} must be text.")
    for item_id, decision in store["claims"].items():
        if not isinstance(decision, dict) or decision.get("status", "pending") not in CLAIM_STATES:
            raise ValueError(f"Research claim decision {item_id!r} is malformed.")
        if not isinstance(decision.get("reviewer_notes", ""), str):
            raise ValueError(f"Research claim notes for {item_id!r} must be text.")
    changed = False
    for source in report.get("sources") or []:
        sid = source["source_id"]
        current = store.setdefault("sources", {}).get(sid)
        if current is None or current.get("fingerprint") != source.get("fingerprint"):
            store["sources"][sid] = {"status": "pending", "reviewer_notes": "",
                                     "fingerprint": source.get("fingerprint", ""), "reviewed_at": None}
            changed = True
    for claim in report.get("claims") or []:
        cid = claim["claim_id"]
        current = store.setdefault("claims", {}).get(cid)
        if current is None or current.get("fingerprint") != claim.get("fingerprint"):
            store["claims"][cid] = {"status": "pending", "reviewer_notes": "",
                                    "fingerprint": claim.get("fingerprint", ""), "reviewed_at": None}
            changed = True
    if changed or not path.is_file():
        # Any source/claim addition or edit invalidates the prior whole-script coverage attestation.
        store["coverage_reviewed"] = False
        store["coverage_notes"] = ""
        store["updated_at"] = _now()
        _atomic_write(path, store)
    return store


def _save_decision(build_dir: str | Path, report: dict[str, Any], kind: str,
                   item_id: str, status: str, notes: str) -> dict[str, Any]:
    if kind not in {"sources", "claims"}:
        raise ValueError("Review kind must be sources or claims.")
    valid_states = SOURCE_STATES if kind == "sources" else CLAIM_STATES
    if status not in valid_states:
        raise ValueError(f"Invalid {kind[:-1]} review status: {status}.")
    if len(str(notes or "").strip()) < 5:
        raise ValueError("Reviewer notes of at least five characters are required.")
    id_key = "source_id" if kind == "sources" else "claim_id"
    items = {row[id_key]: row for row in report.get(kind) or []}
    if item_id not in items:
        raise ValueError(f"Unknown {kind[:-1]} ID: {item_id}.")
    if kind == "claims" and status == "verified":
        if not items[item_id].get("valid_source_ids") or not items[item_id].get("evidence"):
            raise ValueError("A claim cannot be marked verified without linked source IDs and an evidence note.")
    store = load_research_reviews(build_dir, report)
    store[kind][item_id].update({"status": status, "reviewer_notes": str(notes).strip()[:2000],
                                 "reviewed_at": _now()})
    store["updated_at"] = _now()
    _atomic_write(_decision_path(build_dir), store)
    return store[kind][item_id]


def save_source_review(build_dir: str | Path, report: dict[str, Any], source_id: str,
                       status: str, notes: str) -> dict[str, Any]:
    return _save_decision(build_dir, report, "sources", source_id, status, notes)


def save_claim_review(build_dir: str | Path, report: dict[str, Any], claim_id: str,
                      status: str, notes: str) -> dict[str, Any]:
    return _save_decision(build_dir, report, "claims", claim_id, status, notes)


def save_coverage_review(build_dir: str | Path, report: dict[str, Any],
                         reviewed: bool, notes: str) -> dict[str, Any]:
    if reviewed and len(str(notes or "").strip()) < 10:
        raise ValueError("Coverage review notes of at least ten characters are required.")
    store = load_research_reviews(build_dir, report)
    store["coverage_reviewed"] = bool(reviewed)
    store["coverage_notes"] = str(notes or "").strip()[:3000]
    store["updated_at"] = _now()
    _atomic_write(_decision_path(build_dir), store)
    return store


def audit_research_reviews(report: dict[str, Any], store: dict[str, Any]) -> dict[str, Any]:
    issues = []
    if not report.get("claims"):
        issues.append({"level": "error", "message": "A structured claim ledger is required."})
    for source in report.get("sources") or []:
        decision = (store.get("sources") or {}).get(source["source_id"], {})
        if decision.get("status") != "reviewed" or len(str(decision.get("reviewer_notes") or "").strip()) < 5:
            issues.append({"level": "error", "source_id": source["source_id"],
                           "message": "Source has not been opened and reviewed with notes."})
        if not source.get("url_valid"):
            issues.append({"level": "error", "source_id": source["source_id"],
                           "message": "Source URL is invalid; replace it with a verified source."})
        if not source.get("metadata_complete"):
            issues.append({"level": "error", "source_id": source["source_id"],
                           "message": "Source title and publisher are required for a usable citation."})
    for claim in report.get("claims") or []:
        decision = (store.get("claims") or {}).get(claim["claim_id"], {})
        if claim.get("traceability_status") != "source_linked_pending_human_review":
            issues.append({"level": "error", "claim_id": claim["claim_id"],
                           "message": "Claim is missing linked source evidence."})
        if decision.get("status") != "verified" or len(str(decision.get("reviewer_notes") or "").strip()) < 5:
            issues.append({"level": "error", "claim_id": claim["claim_id"],
                           "message": f"Claim review status is '{decision.get('status', 'pending')}'; verify it against the cited source or correct/remove it."})
    if not store.get("coverage_reviewed") or len(str(store.get("coverage_notes") or "").strip()) < 10:
        issues.append({"level": "error", "message": "A human has not confirmed that all material factual claims in the complete narration are represented in the claim ledger."})
    return {
        "source_count": len(report.get("sources") or []),
        "claim_count": len(report.get("claims") or []),
        "reviewed_source_count": sum(1 for item in (store.get("sources") or {}).values() if item.get("status") == "reviewed"),
        "verified_claim_count": sum(1 for item in (store.get("claims") or {}).values() if item.get("status") == "verified"),
        "coverage_reviewed": bool(store.get("coverage_reviewed")),
        "publication_ready": not issues,
        "issues": issues,
        "notice": report.get("notice", "Human source and claim verification is required."),
    }


def append_source_citations_to_description(script: dict[str, Any]) -> dict[str, Any]:
    """Append source links to the YouTube description without claiming they are verified."""
    description = str(script.get("description") or "").strip()
    sources = script.get("sources") or []
    rows = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "").strip()
        if not _normal_url(url):
            continue
        title = " ".join(str(source.get("title") or "Source").split())[:300]
        publisher = " ".join(str(source.get("publisher") or "Publisher unknown").split())[:160]
        published = " ".join(str(source.get("published_at") or "Date not supplied").split())[:80]
        rows.append(f"- {title} — {publisher} ({published}): {url}")
    if rows and "Sources & further reading" not in description:
        description = (description + "\\n\\nSources & further reading:\\n" + "\\n".join(rows)).strip()
    script["description"] = description
    return script


def write_research_verification_report(script: dict[str, Any],
                                       discovery_sources: list[dict] | None,
                                       build_dir: str | Path) -> dict[str, Any]:
    root = Path(build_dir)
    report = build_research_verification_report(script, discovery_sources)
    _atomic_write(root / "research_verification_report.json", report)
    load_research_reviews(root, report)
    return report


def assert_research_review_ready(build_dir: str | Path) -> dict[str, Any]:
    root = Path(build_dir)
    path = root / "research_verification_report.json"
    if not path.is_file():
        raise ValueError(f"Research verification report is missing from {root}; generate the claim ledger first.")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read research verification report {path}: {exc}") from exc
    if not isinstance(report, dict):
        raise ValueError(f"Research verification report {path} is malformed.")
    store = load_research_reviews(root, report)
    result = audit_research_reviews(report, store)
    if not result["publication_ready"]:
        details = "; ".join(
            f"{item.get('claim_id') or item.get('source_id') or 'research'}: {item['message']}"
            for item in result["issues"]
        )
        raise ValueError("Long-form publication is blocked by research verification: " + details)
    return result
