"""Multi-source footage engine: search every provider, rank one pool, validate, select.

One scene is served like this:

1. Every enabled provider is searched at the same time, each in its own worker
   and each inside its own time budget. A provider that is slow, rate limited or
   offline costs the scene nothing but its own results.
2. All leads go into one pool. The same item found twice (by two queries, or by
   two providers pointing at one file) is kept once.
3. Leads are filtered by the single licence policy in ``core.footage_licence``
   and by relevance to the scene, then ranked by relevance, technical quality,
   licence clarity and how much of the documentary each provider already supplied.
4. Candidates are downloaded in rank order. A file counts only after ffprobe
   validation; a file whose content was already used in this documentary is
   refused.
5. When nothing qualifies the scene is reported as ``needs_manual_review``.
   Unrelated footage is never substituted.

Search success alone is never treated as working footage.
"""
from __future__ import annotations

import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.footage_licence import PUBLISHABLE_STATES, classify_licence, provenance_gaps
from core.footage_report import ProviderReport, current_report, provider_scope, redact

HASH_PREFIX = "sha256:"


def _number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return float(default)


@dataclass
class Provider:
    """One footage source behind the common interface.

    ``search(query)`` returns ``[(media_url, metadata), ...]``. ``status()``
    returns ``(enabled, auth_status, reason)`` without making a request.
    ``after_download(metadata)`` is called once a file from this provider has
    been validated and selected (for example Coverr's required download ping).
    """
    name: str
    search: Callable[[str], list]
    kind: str = "archive"            # archive | stock
    status: Callable[[], tuple] = field(default=lambda: (True, "not_required", ""))
    after_download: Callable[[dict], None] | None = None
    min_relevance: float | None = None


def identity_of(metadata: dict, url: str = "") -> str:
    return f"{metadata.get('provider')}:{metadata.get('asset_id') or url}"


def canonical_url(url: str) -> str:
    """Media URL without scheme, tracking parameters or credentials, for duplicate detection."""
    from urllib.parse import parse_qsl, urlencode, urlsplit
    parts = urlsplit(str(url or ""))
    keep = [(key, value) for key, value in parse_qsl(parts.query)
            if not key.lower().startswith("utm_") and key.lower() not in {"api_key", "key", "token", "wskey"}]
    return f"{parts.netloc.lower()}{parts.path}" + (f"?{urlencode(keep)}" if keep else "")


def provider_usage(used_ids) -> Counter:
    """How many scenes of this documentary each provider already supplied."""
    counts: Counter = Counter()
    for entry in used_ids or ():
        text = str(entry)
        if text.startswith(HASH_PREFIX) or ":" not in text:
            continue
        counts[text.split(":", 1)[0].strip().lower()] += 1
    return counts


def technical_points(metadata: dict) -> float:
    """0..1 from the size the provider declared; unknown sizes get a neutral 0.4."""
    width = int(metadata.get("declared_width") or metadata.get("width") or 0)
    height = int(metadata.get("declared_height") or metadata.get("height") or 0)
    if not width or not height:
        return 0.4
    short = min(width, height)
    if short >= 1080:
        return 1.0
    if short >= 720:
        return 0.8
    if short >= 480:
        return 0.55
    if short >= 360:
        return 0.4
    return 0.2


def rank_score(relevance: float, metadata: dict, usage: Counter, total_selected: int,
               scene_match: float = 0.0) -> dict:
    """Composite rank. Relevance dominates; the other terms only order close candidates.

    ``scene_match`` is how much of the scene description the item's own text covers,
    so of two items matching the search words the one about this scene ranks first.
    """
    technical = technical_points(metadata)
    licence = 1.0 if metadata.get("licence_state") == "eligible" else 0.7
    share = (usage.get(str(metadata.get("provider") or "").strip().lower(), 0) / total_selected
             if total_selected else 0.0)
    diversity_weight = _number("FOOTAGE_DIVERSITY_WEIGHT", 0.25)
    score = (0.55 * relevance + 0.15 * scene_match + 0.18 * technical + 0.05 * licence
             - diversity_weight * share)
    return {"rank_score": round(score, 4), "relevance": round(relevance, 3),
            "scene_match": round(scene_match, 3), "technical": technical,
            "licence_clarity": licence, "provider_share_before": round(share, 3)}


def _search_one(provider: Provider, queries: list[str], report: ProviderReport, budget: float) -> list:
    """Run every query against one provider, inside that provider's time budget."""
    leads, started, errors = [], time.monotonic(), 0
    with provider_scope(provider.name, report):
        for query in queries:
            if time.monotonic() - started > budget:
                report.fail(provider.name, f"search budget of {budget:.0f}s used up; remaining queries skipped",
                            search_status="partial_timeout")
                break
            report.add(provider.name, "searches")
            try:
                found = provider.search(query) or []
                errors = 0
            except Exception as exc:  # one catalogue failing must not stop the others
                errors += 1
                report.fail(provider.name, f"{type(exc).__name__}: {exc}", search_status="error")
                print(f"  footage provider skipped ({provider.name}): {type(exc).__name__}: {redact(exc)}")
                if errors >= 2:
                    break  # failing the same way twice in a row; stop asking for this scene
                continue
            for url, metadata in found:
                if isinstance(url, str) and isinstance(metadata, dict):
                    leads.append((query, url, metadata))
    return leads


def search_all(providers: list[Provider], queries: list[str], report: ProviderReport) -> list:
    """Search all enabled providers concurrently. Returns ``[(provider, query, url, metadata)]``."""
    active = []
    for provider in providers:
        try:
            enabled, auth_status, reason = provider.status()
        except Exception as exc:
            enabled, auth_status, reason = False, "unknown", f"{type(exc).__name__}: {exc}"
        entry = report.provider(provider.name)
        if entry["auth_status"] != "rejected":
            report.set(provider.name, auth_status=auth_status)
        if not enabled:
            report.set(provider.name, enabled=False, disabled_reason=reason,
                       search_status=entry["search_status"] if entry["search_status"] != "not_attempted" else "skipped")
            continue
        report.set(provider.name, enabled=True)
        active.append(provider)
    if not active or not queries:
        return []
    budget = _number("FOOTAGE_PROVIDER_SEARCH_SECONDS", 60)
    workers = int(max(1, min(len(active), _number("FOOTAGE_SEARCH_WORKERS", 8))))
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="footage-search")
    futures = {pool.submit(_search_one, provider, queries, report, budget): provider for provider in active}
    # A request already in flight when the budget ends is allowed to finish its timeout.
    done, pending = wait(futures, timeout=budget + _number("FOOTAGE_SEARCH_GRACE_SECONDS", 45))
    pool.shutdown(wait=False, cancel_futures=True)
    leads = []
    for future in done:
        provider = futures[future]
        try:
            found = future.result()
        except Exception as exc:
            report.fail(provider.name, f"{type(exc).__name__}: {exc}", search_status="error")
            continue
        entry = report.provider(provider.name)
        if entry["search_status"] in ("not_attempted", "skipped"):
            report.set(provider.name, search_status="ok" if found else "no_results")
        elif found:
            report.set(provider.name, search_status="ok")
        report.add(provider.name, "downloadable_candidates", len(found))
        leads.extend((provider, query, url, metadata) for query, url, metadata in found)
    for future in pending:
        report.fail(futures[future].name, "search did not finish inside the time budget; results ignored",
                    search_status="timeout")
    return leads


def acquire(queries: list[str], path, used_ids: set, *, scene_prompt: str = "", providers: list[Provider],
            download: Callable, relevance: Callable, min_relevance: float = 0.25,
            report: ProviderReport | None = None, scene_label: str = "",
            prepare: Callable[[dict, float, str], None] | None = None) -> bool:
    """Find, download and validate one clip for one scene. Returns ``True`` when a file was selected."""
    report = report or current_report()
    path = Path(path)
    used_ids = used_ids if used_ids is not None else set()
    label = scene_label or path.stem
    started = time.monotonic()
    leads = search_all(providers, queries, report)

    # One pool, each item once: by provider identity and by the file it points at.
    pool: dict[str, dict] = {}
    by_url: dict[str, str] = {}
    scene_text = " ".join(str(query) for query in queries)
    for provider, query, url, metadata in leads:
        score = max(float(relevance(query, metadata) or 0.0),
                    0.9 * float(relevance(scene_text, metadata) or 0.0) if len(queries) > 1 else 0.0)
        identity = identity_of(metadata, url)
        key = by_url.get(canonical_url(url), identity)
        previous = pool.get(key)
        if previous is None or score > previous["relevance"]:
            pool[key] = {"provider": provider, "query": query, "url": url, "metadata": metadata,
                         "relevance": score, "identity": identity}
        by_url.setdefault(canonical_url(url), key)

    usage = provider_usage(used_ids)
    total_selected = sum(usage.values())
    rejected = Counter()
    ranked = []
    for candidate in pool.values():
        metadata, provider = candidate["metadata"], candidate["provider"]
        if candidate["identity"] in used_ids:
            rejected["already used in this documentary"] += 1
            continue
        decision = classify_licence(metadata)
        metadata["licence_state"] = decision["state"]
        metadata["licence_reason"] = decision["reason"]
        if decision["state"] not in PUBLISHABLE_STATES:
            rejected[f"licence {decision['state']}"] += 1
            report.reject(provider.name, f"licence {decision['state']}: {decision['reason']}"[:140])
            continue
        gaps = provenance_gaps(metadata, decision)
        if gaps:
            rejected["source record incomplete (" + ", ".join(gaps) + ")"] += 1
            report.reject(provider.name, "source record incomplete: " + ", ".join(gaps))
            continue
        floor = provider.min_relevance if provider.min_relevance is not None else min_relevance
        if candidate["relevance"] < floor:
            rejected["not relevant enough to the scene"] += 1
            continue
        scene_match = float(relevance(scene_prompt, metadata) or 0.0) if scene_prompt else 0.0
        candidate["rank"] = rank_score(candidate["relevance"], metadata, usage, total_selected, scene_match)
        ranked.append(candidate)
    ranked.sort(key=lambda item: -item["rank"]["rank_score"])

    attempts = int(max(1, _number("FOOTAGE_MAX_DOWNLOAD_ATTEMPTS", 6)))
    tried = []
    for candidate in ranked[:attempts]:
        provider, metadata, url = candidate["provider"], candidate["metadata"], candidate["url"]
        metadata["relevance_score"] = round(candidate["relevance"], 3)
        metadata["rank_details"] = candidate["rank"]
        metadata["scene_prompt"] = str(scene_prompt)[:500]
        metadata["matched_query"] = candidate["query"]
        if prepare:
            prepare(metadata, candidate["relevance"], candidate["query"])
        report.add(provider.name, "download_attempts")
        try:
            with provider_scope(provider.name, report):
                ok = bool(download(url, path, metadata))
        except Exception as exc:
            ok = False
            metadata["validation"] = {"ok": False, "status": "download_failed",
                                      "reason": redact(f"{type(exc).__name__}: {exc}")[:300]}
        validation = metadata.get("validation") if isinstance(metadata.get("validation"), dict) else {}
        if not ok:
            status = validation.get("status") or "download_failed"
            if status not in ("download_failed", "no_media_url", "low_score"):
                report.add(provider.name, "invalid_media")
            reason = validation.get("reason") or "download or validation failed"
            report.fail(provider.name, f"{status}: {reason}")
            tried.append({"provider": provider.name, "asset_id": metadata.get("asset_id"),
                          "outcome": status, "reason": reason})
            rejected[f"file rejected ({status})"] += 1
            continue
        report.add(provider.name, "download_successes")
        digest = str(metadata.get("media_sha256") or "")
        if digest and HASH_PREFIX + digest in used_ids:
            # Same picture under another catalogue entry or another provider.
            path.unlink(missing_ok=True)
            path.with_suffix(path.suffix + ".source.json").unlink(missing_ok=True)
            tried.append({"provider": provider.name, "asset_id": metadata.get("asset_id"),
                          "outcome": "duplicate_content", "reason": "identical file already used in this documentary"})
            rejected["identical file already used"] += 1
            continue
        used_ids.add(candidate["identity"])
        if digest:
            used_ids.add(HASH_PREFIX + digest)
        report.add(provider.name, "eligible_assets")
        report.add(provider.name, "selected_scenes")
        if provider.after_download:
            try:
                provider.after_download(metadata)
            except Exception as exc:
                report.note(f"{provider.name}: post-download step failed: {type(exc).__name__}: {exc}")
        report.scene({
            "scene": label, "status": "selected", "queries": queries[:12],
            "candidates_found": len(pool), "candidates_eligible": len(ranked),
            "search_seconds": round(time.monotonic() - started, 1),
            "reason": (f"best of {len(ranked)} eligible candidate(s) from "
                       f"{len({item['provider'].name for item in ranked})} provider(s)"),
            "selected": {
                "provider": metadata.get("provider"), "asset_id": metadata.get("asset_id"),
                "title": metadata.get("title"), "page_url": metadata.get("page_url"),
                "licence": metadata.get("licence"), "licence_state": metadata.get("licence_state"),
                "relevance_score": metadata.get("relevance_score"), "rank": candidate["rank"],
                "width": metadata.get("width"), "height": metadata.get("height"),
                "duration_seconds": metadata.get("duration_seconds"),
                "media_sha256": digest, "local_path": str(path),
            },
            "rejected_before_selection": tried,
        })
        return True

    if rejected:
        detail = "; ".join(f"{count} {why}" for why, count in rejected.most_common())
        reason = f"{len(pool)} candidate(s) found, none usable: {detail}"
    elif leads:
        reason = "candidates were found but none could be ranked"
    else:
        reason = "no provider returned a candidate for these queries"
    report.scene({
        "scene": label, "status": "needs_manual_review", "queries": queries[:12],
        "candidates_found": len(pool), "candidates_eligible": len(ranked),
        "search_seconds": round(time.monotonic() - started, 1),
        "reason": reason, "selected": None, "rejected_before_selection": tried,
    })
    print(f"  no usable footage for this scene ({reason}); marked for manual review")
    return False
