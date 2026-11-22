#!/usr/bin/env python3
"""Generate the protected-workflow manifest without touching production files.

Milestone 1 of the Engineering Physics studio audit freezes the existing
production surface.  This script reads the checkout and writes a deterministic
JSON manifest describing:

* every existing workflow and its triggers, schedules, dispatch inputs,
  secrets, variables, artifact names and privacy-relevant lines;
* exact hashes for workflow/configuration files that must not change; and
* hashes of existing production modules, so a later milestone has to update the
  manifest deliberately (and explain why) before changing their behaviour.

It intentionally uses only the Python standard library: it must run in the
minimal news-collection environment as well as in full CI.  Additions of new
files do not invalidate the manifest; deletion or modification of a listed file
does.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
OUTPUT = ROOT / "docs" / "audit" / "protected-workflow-manifest.json"
BASELINE_COMMIT = "bdb6f82180a22e5ad7532372c3cbd1dc531d95c9"
AUDIT_DATE = "2026-11-19"

NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.M)
ON_RE = re.compile(r"^on\s*:\s*$", re.M)
CRON_RE = re.compile(r"^\s*(?:-\s*)?cron:\s*['\"]?([^'\"\n#]+?)['\"]?(?:\s+#.*)?$", re.M)
# Use horizontal whitespace only after the colon: `\s*` would consume the
# newline and swallow the next two-space trigger key into the capture group.
TRIGGER_RE = re.compile(r"^  ([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:[ \t]*(.*)$", re.M)
SECRET_RE = re.compile(r"secrets\.([A-Za-z0-9_]+)")
VAR_RE = re.compile(r"vars\.([A-Za-z0-9_]+)")
USES_RE = re.compile(r"^[ \t]*(?:-[ \t]*)?uses:[ \t]*(\S+)", re.M)
RUNS_ON_RE = re.compile(r"^\s*runs-on:\s*(.+?)\s*$", re.M)
TIMEOUT_RE = re.compile(r"^\s*timeout-minutes:\s*(.+?)\s*$", re.M)
ARTIFACT_ACTION_RE = re.compile(r"uses:\s*actions/(upload|download)-artifact@(\S+)")

UPLOAD_MARKERS = (
    "upload_documentary_artifact.py",
    "release_review_package.py",
    "--upload-latest",
    "--auto-private-release",
    "run_daily_news_short.py --upload",
    "upload_approved_news_short.py",
    "uploader.upload",
    "videos().insert",
    "--upload ",
)
PRIVACY_MARKERS = ("PRIVACY:", "ALLOW_PUBLIC_PUBLISH:", "privacy_status", "privacyStatus", "disable_scheduling")
INPUT_FIELDS = {"description", "required", "default", "type", "options"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_sha(relative_path: str) -> str:
    return hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _on_block(text: str) -> str:
    match = ON_RE.search(text)
    if not match:
        return ""
    lines = text.splitlines()
    start = text[:match.start()].count("\n")
    block = [lines[start]]
    for line in lines[start + 1:]:
        if line and not line[0].isspace() and not line.lstrip().startswith("#"):
            break
        block.append(line)
    while block and not block[-1].strip():
        block.pop()
    return "\n".join(block).rstrip()


def _dispatch_inputs(on_block: str) -> dict[str, dict[str, Any]]:
    lines = on_block.splitlines()
    indents = [(len(line) - len(line.lstrip()), line)
               for line in lines if line.strip() and not line.lstrip().startswith("#")]
    try:
        dispatch_at = next(i for i, (indent, line) in enumerate(indents)
                           if re.match(r"^workflow_dispatch\s*:\s*$", line.strip()))
    except StopIteration:
        return {}
    dispatch_indent = indents[dispatch_at][0]
    try:
        inputs_at = next(i for i, (indent, line) in enumerate(indents[dispatch_at + 1:], dispatch_at + 1)
                         if indent > dispatch_indent and re.match(r"^inputs\s*:\s*$", line.strip()))
    except StopIteration:
        return {}
    inputs_indent = indents[inputs_at][0]
    name_indent = inputs_indent + 2
    field_indent = name_indent + 2

    inputs: dict[str, dict[str, Any]] = {}
    current: str | None = None
    current_key: str | None = None
    for indent, line in indents[inputs_at + 1:]:
        if indent <= inputs_indent:
            break
        if indent == name_indent:
            match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*)$", line.strip())
            if not match:
                continue
            current = match.group(1)
            inputs[current] = {}
            current_key = None
            remainder = match.group(2).strip()
            if remainder and remainder not in {"{}", "|", ">"}:
                inputs[current]["value"] = _unquote(remainder)
            continue
        if current is None or indent < field_indent:
            continue
        field = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*)$", line.strip())
        if field and indent == field_indent:
            current_key = field.group(1)
            if current_key in INPUT_FIELDS:
                inputs[current][current_key] = _unquote(field.group(2))
            else:
                current_key = None
            continue
        if indent > field_indent and current_key in INPUT_FIELDS:
            option = re.match(r"^-\s+(.+?)\s*$", line.strip())
            if option:
                existing = inputs[current].setdefault(current_key, [])
                if isinstance(existing, list):
                    existing.append(_unquote(option.group(1)))

    normalised: dict[str, dict[str, Any]] = {}
    for key, value in inputs.items():
        entry = {k: value[k] for k in sorted(value) if k != "options"}
        options = value.get("options")
        if isinstance(options, list):
            entry["options"] = options
        elif isinstance(options, str) and options.startswith("["):
            entry["options"] = [_unquote(x) for x in options.strip("[]").split(",") if x.strip()]
        elif options:
            entry["options"] = options
        normalised[key] = entry
    return normalised


def _artifact_names(text: str) -> list[str]:
    lines = text.splitlines()
    names: set[str] = set()
    for index, line in enumerate(lines):
        match = ARTIFACT_ACTION_RE.search(line)
        if not match:
            continue
        indent = len(line) - len(line.lstrip())
        for following in lines[index + 1: index + 12]:
            if not following.strip():
                continue
            next_indent = len(following) - len(following.lstrip())
            if next_indent <= indent:
                continue  # `with:` or the next step; only a deeper `name:` belongs to this action.
            name_match = re.match(r"^\s+name:\s*(.+?)\s*$", following)
            if name_match:
                names.add(_unquote(name_match.group(1)))
                break
    return sorted(names)


def _notable_lines(text: str, markers: tuple[str, ...]) -> list[str]:
    return sorted({line.strip() for line in text.splitlines()
                   if any(marker in line for marker in markers) and not line.lstrip().startswith("#")})


def _workflow(name: str, text: str) -> dict[str, Any]:
    on_block = _on_block(text)
    name_match = NAME_RE.search(text)
    triggers = []
    for trigger, _ in TRIGGER_RE.findall(on_block):
        if trigger not in triggers:
            triggers.append(trigger)
    secrets = sorted(set(SECRET_RE.findall(text)))
    variables = sorted(set(VAR_RE.findall(text)))
    return {
        "sha256": _sha(text),
        "display_name": _unquote(name_match.group(1)) if name_match else "",
        "on_sha256": _sha(on_block),
        "triggers": triggers,
        "schedules": CRON_RE.findall(on_block),
        "dispatch_inputs": _dispatch_inputs(on_block),
        "secrets": secrets,
        "variables": variables,
        "artifact_names": _artifact_names(text),
        "actions": sorted(set(USES_RE.findall(text))),
        "runs_on": sorted(set(_unquote(x) for x in RUNS_ON_RE.findall(text))),
        "timeout_minutes": sorted(set(_unquote(x) for x in TIMEOUT_RE.findall(text))),
        "privacy_lines": _notable_lines(text, PRIVACY_MARKERS),
        "upload_lines": _notable_lines(text, UPLOAD_MARKERS),
    }


def build_manifest() -> dict[str, Any]:
    workflow_paths = sorted(WORKFLOWS.glob("*.yml"))
    configs = sorted(p for p in (ROOT / ".github").iterdir() if p.is_file())
    workflows = {p.name: _workflow(p.name, p.read_text(encoding="utf-8")) for p in workflow_paths}

    immutable = {str(p.relative_to(ROOT)): _file_sha(str(p.relative_to(ROOT))) for p in [*workflow_paths, *configs]}

    monitored: dict[str, str] = {}
    for pattern in ("core/*.py", "scripts/*.py", "tests/footage_*.py",
                    "src/__init__.py", "src/production_v2/*.py"):
        for path in sorted(ROOT.glob(pattern)):
            if path.name == "__init__.py" and pattern == "tests/footage_*.py":
                continue
            rel = str(path.relative_to(ROOT))
            # The long-form voice audition is an isolated diagnostic utility, not
            # a production module. Keep it out of the protected production hash set.
            if rel == "scripts/create_longform_voice_audition.py":
                continue
            monitored[rel] = _file_sha(rel)
    for rel in ("run.py", "run_daily.py", "get_refresh_tokens.py", "dashboard.py",
                "requirements.txt", "requirements-ci.txt", "requirements-dev.txt",
                "schemas/production-manifest-v1.schema.json",
                "schemas/production-manifest-v2.schema.json",
                "schemas/review-decisions-v2.schema.json",
                "config/trusted_sources.json", "config/blocklist_sources.json"):
        monitored[rel] = _file_sha(rel)

    secrets = sorted({secret for wf in workflows.values() for secret in wf["secrets"]})
    variables = sorted({var for wf in workflows.values() for var in wf["variables"]})
    schedules = {name: wf["schedules"] for name, wf in sorted(workflows.items()) if wf["schedules"]}

    return {
        "schema_version": 1,
        "baseline": {
            "commit": BASELINE_COMMIT,
            "branch_audit_date": AUDIT_DATE,
            "default_branch": "main",
            "repository": "ay23-byte/ai-studio-automation",
            "channel_handle": "@engineeringphysics-n5g",
        },
        "invariants": {
            "production_privacy_status": "private",
            "public_publish_allowed_in_protected_workflows": False,
            "automatic_public_release": False,
            "protected_workflow_count": len(workflow_paths),
            "protected_config_count": len(configs),
        },
        "schedules": schedules,
        "secrets": secrets,
        "variables": variables,
        "artifact_names": sorted({name for wf in workflows.values() for name in wf["artifact_names"]}),
        "immutable_files": immutable,
        "monitored_files": monitored,
        "workflows": workflows,
    }


def main() -> int:
    manifest = build_manifest()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)} "
          f"({len(manifest['workflows'])} workflows, {len(manifest['immutable_files'])} immutable files, "
          f"{len(manifest['monitored_files'])} monitored files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
