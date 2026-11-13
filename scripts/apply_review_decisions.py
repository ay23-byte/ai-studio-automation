"""Check human review decisions against a review package and record them.

    python scripts/apply_review_decisions.py <package_dir> <decisions.json>

The decisions are evaluated first and nothing is written unless they authorise
this exact package.  Recording a decision never edits rights evidence or QA
reports: those were fixed when the package was built.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import review_contract as contract


class DecisionsRejected(ValueError):
    """The decisions do not authorise release of this package."""

    def __init__(self, report: dict):
        self.report = report
        reasons = report.get("blocking") or ["No reason recorded."]
        super().__init__("The review decisions do not authorise release of this package:\n- "
                         + "\n- ".join(reasons[:40])
                         + (f"\n- ... and {len(reasons) - 40} more" if len(reasons) > 40 else ""))


def load_decisions(path) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise contract.ContractError(f"Cannot read the review decisions: {exc}") from exc
    return contract.load_decisions_text(text)


def apply_decisions(package_dir, decisions_path, *, decode: bool = True) -> dict:
    """Validate, then record.  Raises :class:`DecisionsRejected` and writes nothing on refusal."""
    root = Path(package_dir).resolve()
    decisions = load_decisions(decisions_path)
    report = contract.evaluate_release(root, decisions, decode=decode)
    if not report["allowed"]:
        raise DecisionsRejected(report)
    manifest = contract.read_manifest(root)
    contract.write_json(root / contract.DECISIONS_NAME, decisions)
    checklist = {name: "approved" for name in contract.CONFIRMATIONS}
    checklist.update({
        "human_review_status": "approved", "reviewer": report["reviewer"],
        "reviewed_at": decisions.get("reviewed_at"), "job_id": report["job_id"],
        "video_sha256": report["video_sha256"], "manifest_fingerprint": report["manifest_fingerprint"],
        "decision_notes": (decisions.get("overall") or {}).get("notes"),
    })
    contract.write_json(root / "review_checklist.json", checklist)
    # The review block is outside the manifest fingerprint, so recording the
    # outcome does not invalidate the approval it records.
    manifest["review"] = {
        "editorial": "approved", "visual": "approved", "source": "approved", "rights": "approved",
        "attribution": "approved", "technical": "PASS", "reviewer": report["reviewer"],
        "reviewed_at": decisions.get("reviewed_at"),
        "decision_notes": (decisions.get("overall") or {}).get("notes"),
        "release_authorization": "authorized_private_upload_of_this_exact_video",
    }
    contract.write_json(root / contract.MANIFEST_NAME, manifest)
    summary = contract.package_status(root, decode=False)
    contract.write_json(root / "package_summary.json", summary)
    scenes = manifest.get("scenes") or []
    return {"status": summary["status"], "job_id": report["job_id"], "reviewer": report["reviewer"],
            "video_sha256": report["video_sha256"], "scene_count": len(scenes),
            "asset_count": sum(len(scene.get("assets") or []) for scene in scenes)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("package_dir")
    parser.add_argument("decisions")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(apply_decisions(args.package_dir, args.decisions), indent=2))
    except ValueError as exc:
        print(f"::error::{str(exc).splitlines()[0]}")
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
