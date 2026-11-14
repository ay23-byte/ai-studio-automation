"""One licence policy for every footage provider.

Before this module the repository had two rule sets: ``asset_licensing`` accepted
some labels that the download gate in ``footage_sources`` refused, so DVIDS and
Library of Congress results were discovered and then never downloaded.

A candidate is classified from the evidence recorded for that exact asset. A
provider name is never evidence, and nothing here claims that "everything on
platform X is free".

States
------
``eligible``                   usable, no attribution duty
``eligible_with_attribution``  usable when the recorded credit is shown
``restricted``                 a licence exists but forbids this use
``unknown``                    not enough evidence to decide
``rejected``                   evidence shows it must not be used

Only the first two pass the publication gate, and even those still need the
human rights review that the review package records.
"""
from __future__ import annotations

import re
from datetime import date

ELIGIBLE = "eligible"
ELIGIBLE_WITH_ATTRIBUTION = "eligible_with_attribution"
RESTRICTED = "restricted"
UNKNOWN = "unknown"
REJECTED = "rejected"

STATES = (ELIGIBLE, ELIGIBLE_WITH_ATTRIBUTION, RESTRICTED, UNKNOWN, REJECTED)
PUBLISHABLE_STATES = (ELIGIBLE, ELIGIBLE_WITH_ATTRIBUTION)

# Explicit marker used wherever a provider did not supply a value.
NOT_PROVIDED = "unknown"

# A work first published in the United States is in the public domain there from
# 1 January of the 96th year after publication (17 U.S.C. 304: 95-year term).
US_PUBLICATION_TERM_YEARS = 95


def us_public_domain_cutoff_year(today: date | None = None) -> int:
    """Latest publication year that is in the US public domain on ``today``."""
    return (today or date.today()).year - US_PUBLICATION_TERM_YEARS - 1


def _normal(value: object) -> str:
    return " ".join(str(value or "").strip().lower().replace("-", " ").replace("_", " ").split())


def _decision(state, reason, *, attribution_required=None, commercial_use=None,
              modification=None, share_alike=False, evidence="", notes=()):
    return {
        "state": state,
        "publishable": state in PUBLISHABLE_STATES,
        "reason": reason,
        # None means "not established"; it is written out as "unknown", never guessed.
        "attribution_required": attribution_required,
        "commercial_use": commercial_use,
        "modification": modification,
        "share_alike": bool(share_alike),
        "evidence": evidence or NOT_PROVIDED,
        "notes": list(notes),
    }


def parse_creative_commons(url_or_label: object) -> dict | None:
    """Read a Creative Commons licence from its URL or short label.

    Returns ``{"code", "version", "label", "url_kind"}`` or ``None``. The version
    is taken from the text; it is never assumed to be 4.0.
    """
    text = str(url_or_label or "").strip()
    low = text.lower()
    if not low:
        return None
    match = re.search(r"creativecommons\.org/(licenses|publicdomain)/([a-z\-]+)(?:/(\d+(?:\.\d+)?))?", low)
    if match:
        family, code, version = match.group(1), match.group(2), match.group(3) or ""
        if family == "publicdomain" or code == "publicdomain":
            if code == "zero":
                return {"code": "cc0", "version": version or "1.0",
                        "label": f"CC0 {version or '1.0'}", "url_kind": "cc0"}
            # /publicdomain/mark/1.0/ and the retired /licenses/publicdomain/ dedication.
            return {"code": "pdm", "version": version, "label": "Public Domain", "url_kind": "public_domain"}
        label = "CC " + code.upper() + (f" {version}" if version else "")
        return {"code": code, "version": version, "label": label, "url_kind": "cc_licence"}
    label = _normal(text)
    if label in {"cc0", "cc0 1.0", "creative commons zero", "cc zero"}:
        return {"code": "cc0", "version": "1.0", "label": "CC0 1.0", "url_kind": "label"}
    match = re.match(r"^cc (by(?: (?:nc|nd|sa))*)(?: (\d+(?:\.\d+)?))?(?: [a-z]{2,10})?$", label)
    if match:
        code = match.group(1).replace(" ", "-")
        version = match.group(2) or ""
        return {"code": code, "version": version,
                "label": "CC " + code.upper() + (f" {version}" if version else ""), "url_kind": "label"}
    return None


_RIGHTS_STATEMENTS = {
    # rightsstatements.org vocabulary used by Europeana and many archives.
    "inc": (RESTRICTED, "in copyright"),
    "inc-ow-eu": (RESTRICTED, "in copyright, EU orphan work"),
    "inc-edu": (RESTRICTED, "in copyright, educational use only"),
    "inc-nc": (RESTRICTED, "in copyright, non-commercial use only"),
    "inc-ruu": (RESTRICTED, "in copyright, rights-holder unlocatable"),
    "noc-cr": (RESTRICTED, "no copyright, contractual restrictions"),
    "noc-nc": (RESTRICTED, "no copyright, non-commercial use only"),
    "noc-oklr": (RESTRICTED, "no copyright, other known legal restrictions"),
    "cne": (UNKNOWN, "copyright not evaluated"),
    "und": (UNKNOWN, "copyright undetermined"),
    "nkc": (UNKNOWN, "no known copyright; the institution gives no assurance"),
}


def _rights_statement(url: str):
    match = re.search(r"rightsstatements\.org/(?:vocab|page)/([a-z\-]+)/", str(url or "").lower())
    if not match:
        return None
    code = match.group(1)
    if code == "noc-us":
        return ELIGIBLE, "no copyright in the United States (rightsstatements.org NoC-US)"
    return _RIGHTS_STATEMENTS.get(code, (UNKNOWN, f"unrecognised rights statement {code}"))


_RESTRICTIVE_TEXT = re.compile(
    r"all rights reserved|in copyright|copyrighted|non ?commercial|editorial use only|"
    r"educational (?:use|purposes) only|no derivatives")


def classify_licence(metadata: dict | None) -> dict:
    """Classify one asset's recorded licence evidence into a policy state."""
    metadata = metadata or {}
    label = _normal(metadata.get("licence") or metadata.get("license"))
    licence_url = str(metadata.get("licence_url") or metadata.get("license_url") or "").strip()
    provider = _normal(metadata.get("provider"))
    page_url = str(metadata.get("page_url") or metadata.get("url") or "").strip()
    creator = str(metadata.get("creator") or metadata.get("author") or "").strip()

    if metadata.get("third_party_rights_flag"):
        return _decision(REJECTED, "third-party or copyright marker found in the item metadata",
                         evidence=str(metadata.get("third_party_rights_flag"))[:300])
    if metadata.get("ai_generated") is True and provider not in {"lablens/ai studio local graphic renderer"}:
        return _decision(REJECTED, "AI-generated library content cannot be presented as documentary footage",
                         evidence="provider flagged the asset as AI-generated")

    if not label and not licence_url:
        return _decision(UNKNOWN, "no licence name or licence URL was recorded for this asset")

    # A restrictive licence name wins over whatever URL sits beside it: when the two
    # disagree, the record is wrong somewhere and the safe reading is the stricter one.
    if _RESTRICTIVE_TEXT.search(label):
        return _decision(RESTRICTED, f"licence text restricts this use: {label[:120]}",
                         evidence=licence_url or page_url)
    named, linked = parse_creative_commons(label), parse_creative_commons(licence_url)
    if named and linked and named["code"] != linked["code"]:
        stricter = {"nc", "nd"} & set(named["code"].split("-"))
        return _decision(
            REJECTED if stricter else UNKNOWN,
            f"licence name ({named['label']}) and licence URL ({linked['label']}) disagree",
            evidence=licence_url)

    statement = _rights_statement(licence_url)
    if statement:
        state, reason = statement
        if state == ELIGIBLE:
            return _decision(ELIGIBLE, reason, attribution_required=False, commercial_use=True,
                             modification=True, evidence=licence_url)
        return _decision(state, reason, evidence=licence_url)

    cc = parse_creative_commons(licence_url) or parse_creative_commons(label)
    if cc:
        parts = set(cc["code"].split("-"))
        evidence = licence_url or f"licence label: {metadata.get('licence')}"
        if cc["code"] in {"cc0", "pdm"}:
            return _decision(ELIGIBLE, f"{cc['label']} recorded for this item",
                             attribution_required=False, commercial_use=True, modification=True,
                             evidence=evidence)
        if "nc" in parts:
            return _decision(REJECTED, f"{cc['label']} forbids commercial use",
                             attribution_required=True, commercial_use=False,
                             modification="nd" not in parts, evidence=evidence)
        if "nd" in parts:
            return _decision(REJECTED, f"{cc['label']} forbids derivative works; editing into a film is a derivative",
                             attribution_required=True, commercial_use=True, modification=False,
                             evidence=evidence)
        notes = []
        if not licence_url:
            # The licence is known by name. The missing URL is a gap in the record
            # (see provenance_gaps), which blocks automatic use until it is supplied.
            notes.append("licence URL missing: the licence is named but not linked for this item")
        if not page_url:
            notes.append("source page URL missing: the credit this licence requires cannot be completed without it")
        if not creator:
            notes.append("creator not supplied by the source; credit must say so and link the source page")
        if "sa" in parts:
            notes.append("share-alike: adaptations must carry the same licence; a human must accept this obligation")
        return _decision(ELIGIBLE_WITH_ATTRIBUTION, f"{cc['label']} recorded for this item",
                         attribution_required=True, commercial_use=True, modification=True,
                         share_alike="sa" in parts, evidence=evidence, notes=notes)

    if label in {"public domain", "public domain mark", "public domain mark 1.0", "pd"}:
        return _decision(ELIGIBLE, "public-domain statement recorded for this item",
                         attribution_required=False, commercial_use=True, modification=True,
                         evidence=licence_url or f"licence label: {metadata.get('licence') or metadata.get('license')}",
                         notes=[] if licence_url else
                         ["rights URL missing: the public-domain claim is named but not linked for this item"])

    if label == "nasa media usage guidelines":
        if provider != "nasa":
            return _decision(UNKNOWN, "NASA guidelines label on an asset that is not from NASA")
        return _decision(
            ELIGIBLE_WITH_ATTRIBUTION,
            "NASA media usage guidelines; NASA centre recorded and no third-party marker found",
            attribution_required=True, commercial_use=True, modification=True,
            evidence=licence_url or "https://www.nasa.gov/nasa-brand-center/images-and-media/",
            notes=["NASA material must not imply endorsement; identifiable people and NASA insignia have separate rules"])

    if label == "dvids api commercial use terms":
        if provider != "dvids":
            return _decision(UNKNOWN, "DVIDS terms label on an asset that is not from DVIDS")
        return _decision(
            ELIGIBLE_WITH_ATTRIBUTION,
            "DVIDS API Terms of Service I.B: assets accessible via the API are free for commercial use",
            attribution_required=True, commercial_use=True, modification=True,
            evidence=licence_url or "https://api.dvidshub.net/docs/tos",
            notes=["credit the named creator and DVIDS; DoD visual information does not imply endorsement",
                   "unpublished DVIDS assets must be removed from caches (TOS I.E)"])

    if label == "no known copyright restrictions":
        basis = str(metadata.get("rights_basis") or "").strip()
        if basis:
            return _decision(
                ELIGIBLE_WITH_ATTRIBUTION,
                "holding institution reports no known restrictions and " + basis,
                attribution_required=True, commercial_use=True, modification=True,
                evidence=licence_url or page_url,
                notes=["the institution's advisory is not a warranty; privacy and publicity rights still apply"])
        return _decision(
            UNKNOWN,
            "holding institution reports no known restrictions but gives no item-level assurance",
            evidence=licence_url or page_url)

    if label == "pixabay content license":
        return _decision(
            ELIGIBLE, "Pixabay Content License recorded by the Pixabay API for this item",
            attribution_required=False, commercial_use=True, modification=True,
            evidence=licence_url or "https://pixabay.com/service/license-summary/",
            notes=["must not be redistributed standalone; identifiable people, logos and brands need their own clearance"])

    if label == "coverr license":
        return _decision(
            ELIGIBLE_WITH_ATTRIBUTION,
            "Coverr licence: stock library content is free for commercial use; free API tier requires credit",
            attribution_required=True, commercial_use=True, modification=True,
            evidence=licence_url or "https://coverr.co/license",
            notes=["must not be claimed through YouTube Content ID or redistributed as a file",
                   "report the download with PATCH /videos/:id/stats/downloads (condition of API access)"])

    if label == "mixkit free license":
        if not metadata.get("terms_reviewed"):
            return _decision(UNKNOWN, "Mixkit Free License named but the operator did not confirm the item page")
        return _decision(
            ELIGIBLE, "operator confirmed the Mixkit Free License on this item's page",
            attribution_required=False, commercial_use=True, modification=True,
            evidence=page_url or licence_url)

    if label in {"mixkit restricted license"}:
        return _decision(REJECTED, "Mixkit Restricted License does not cover this use", evidence=page_url)

    if label in {"ncaa written permission", "prasar bharati licensed footage"}:
        scope = _normal(metadata.get("usage_scope"))
        covers = "youtube" in scope and any(term in scope for term in ("commercial", "monetized", "monetised"))
        if metadata.get("licence_document_ref") and covers:
            return _decision(
                ELIGIBLE_WITH_ATTRIBUTION,
                "written permission reference and commercial YouTube scope recorded by the operator",
                attribution_required=True, commercial_use=True, modification=None,
                evidence=str(metadata.get("licence_document_ref")))
        return _decision(RESTRICTED, "written permission reference or commercial YouTube scope missing",
                         evidence=str(metadata.get("licence_document_ref") or ""))

    return _decision(UNKNOWN, f"unrecognised licence label: {label[:120] or 'none'}",
                     evidence=licence_url or page_url)


def provenance_gaps(metadata: dict | None, decision: dict | None = None) -> list[str]:
    """What is missing before this asset can be credited and traced back to its source.

    A licence can be clear while the record is still unusable: a credit needs the
    source page, and a reviewer needs somewhere to check the licence. The footage
    engine and the licence register both apply this list.
    """
    metadata = metadata or {}
    decision = decision or classify_licence(metadata)
    gaps = []
    if not str(metadata.get("page_url") or metadata.get("url") or "").strip():
        gaps.append("source page URL missing")
    if not str(metadata.get("licence_url") or metadata.get("license_url") or "").strip():
        gaps.append("licence URL missing")
    return gaps


def attribution_line(metadata: dict | None, decision: dict | None = None) -> str:
    """Credit text built only from recorded fields; missing fields are named as missing."""
    metadata = metadata or {}
    decision = decision or classify_licence(metadata)
    title = str(metadata.get("title") or "").strip() or "Untitled"
    creator = str(metadata.get("creator") or metadata.get("author") or "").strip()
    provider = str(metadata.get("provider") or "").strip() or "Unknown source"
    licence = str(metadata.get("licence") or metadata.get("license") or "").strip()
    page = str(metadata.get("page_url") or metadata.get("url") or "").strip()
    label = _normal(licence)
    if label.startswith("cc "):
        return f"\"{title}\" by {creator or 'creator not listed by the source'}, {licence}, {page}"
    if label == "nasa media usage guidelines":
        return f"\"{title}\" — {creator or 'NASA'} (NASA media usage guidelines); {page}"
    if label == "dvids api commercial use terms":
        return (f"\"{title}\" — {creator or 'creator not listed'} / DVIDS; {page}. The appearance of U.S. "
                "Department of Defense visual information does not imply or constitute DoD endorsement.")
    if label == "coverr license":
        return f"Video \"{title}\" by {creator or 'Coverr'} on Coverr (https://coverr.co); {page}"
    if label == "pixabay content license":
        return f"\"{title}\" by {creator or 'Pixabay contributor'} — Pixabay Content License; {page}"
    return f"\"{title}\" — {creator + ', ' if creator else ''}{provider} ({licence or 'licence not recorded'}); {page}"


def tri_state(value) -> str:
    """Render True/False/None as yes/no/unknown for reports and CSV."""
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return NOT_PROVIDED
