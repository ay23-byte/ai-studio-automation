#!/usr/bin/env python3
"""Persist small automation state between ephemeral CI runs on a dedicated branch.

GitHub-hosted runners start from a clean checkout, so the duplicate ledger
(``state/publish_ledger.json``) and the news "seen" cache were lost after every
run, which is why the scheduler rebuilt the same subjects. The previous
approach pushed ``topics.json`` straight to ``main`` from the workflow; that
fails under branch protection, races with human edits and triggers CI on every
bot commit.

State now lives on an orphan branch (default ``automation-state``) that holds
only JSON files. ``restore`` copies it into ``state/`` before a run and ``save``
commits it back afterwards, merging ledgers entry-by-entry when another run
pushed first. No media, database or secret is ever written to the branch.

    python scripts/persist_state.py restore
    python scripts/persist_state.py save --message "shorts run 123"
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BRANCH = os.getenv("AI_STUDIO_STATE_BRANCH", "automation-state")
ALLOWED_SUFFIXES = {".json", ".jsonl"}
MAX_FILE_BYTES = 5 * 1024 * 1024


def _git(*args, cwd=None, check=True, capture=True):
    result = subprocess.run(["git", *args], cwd=str(cwd or ROOT), text=True,
                            capture_output=capture)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])} failed: {(result.stderr or '').strip()[:400]}")
    return result


def _state_dir() -> Path:
    return Path(os.getenv("AI_STUDIO_STATE_DIR") or ROOT / "state")


def _state_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.rglob("*")
                  if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES
                  and not path.name.endswith(".tmp") and path.stat().st_size <= MAX_FILE_BYTES)


def _remote_has_branch(remote: str, branch: str) -> bool:
    result = _git("ls-remote", "--exit-code", "--heads", remote, branch, check=False)
    return result.returncode == 0


def merge_ledgers(ours: dict, theirs: dict) -> dict:
    """Union of ledger entries keyed by (kind, key); the newest update wins."""
    merged = {}
    for source in (theirs, ours):
        for entry in (source or {}).get("entries", []) or []:
            if not isinstance(entry, dict):
                continue
            identity = (entry.get("kind"), entry.get("key"))
            current = merged.get(identity)
            if current is None:
                merged[identity] = dict(entry)
                continue
            newer, older = ((entry, current)
                            if str(entry.get("updated_at") or "") >= str(current.get("updated_at") or "")
                            else (current, entry))
            combined = {**older, **newer}
            # Never lose the proof that something was uploaded.
            for sticky in ("youtube_id", "job_id"):
                combined[sticky] = newer.get(sticky) or older.get(sticky)
            combined["source_refs"] = list(dict.fromkeys(
                list(older.get("source_refs") or []) + list(newer.get("source_refs") or [])))
            merged[identity] = {k: v for k, v in combined.items() if v not in (None, [], "")}
    version = max(int((ours or {}).get("version") or 1), int((theirs or {}).get("version") or 1))
    # Deterministic output: merging identical state must not create a new commit.
    stamps = [str((source or {}).get("updated_at") or "") for source in (ours, theirs)]
    stamps += [str(entry.get("updated_at") or "") for entry in merged.values()]
    entries = sorted(merged.values(), key=lambda e: (str(e.get("created_at") or e.get("updated_at") or ""),
                                                     str(e.get("kind")), str(e.get("key"))))
    return {"version": version, "updated_at": max(stamps), "entries": entries}


def _merge_seen(ours, theirs):
    """Merge "seen" caches: dict union or de-duplicated list union."""
    if isinstance(ours, dict) and isinstance(theirs, dict):
        merged = {**theirs, **ours}
        for key in set(ours) & set(theirs):  # e.g. {"ids": [...]}: keep both runs' ids
            if isinstance(ours[key], list) and isinstance(theirs[key], list):
                try:
                    merged[key] = sorted(set(theirs[key]) | set(ours[key]))
                except TypeError:
                    merged[key] = _merge_seen(ours[key], theirs[key])
        return merged
    if isinstance(ours, list) and isinstance(theirs, list):
        seen, result = set(), []
        for item in theirs + ours:
            marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if marker not in seen:
                seen.add(marker)
                result.append(item)
        return result
    return ours


def _merge_file(local: Path, remote: Path) -> None:
    """Write the merge of ``local`` (this run) and ``remote`` (branch tip) into ``remote``."""
    if local.suffix == ".jsonl":
        previous = remote.read_text(encoding="utf-8").splitlines() if remote.exists() else []
        lines = list(dict.fromkeys(previous + local.read_text(encoding="utf-8").splitlines()))
        remote.write_text("\n".join(lines[-5000:]) + "\n", encoding="utf-8")
        return
    try:
        ours = json.loads(local.read_text(encoding="utf-8"))
    except ValueError:
        return  # never publish a corrupt local file over good remote state
    # Files are always re-serialised the same way, so saving unchanged state is a no-op.
    try:
        theirs = json.loads(remote.read_text(encoding="utf-8")) if remote.exists() else None
    except ValueError:
        theirs = None
    if local.name == "publish_ledger.json" and isinstance(ours, dict):
        merged = merge_ledgers(ours, theirs if isinstance(theirs, dict) else {})
    elif theirs is not None:
        merged = _merge_seen(ours, theirs)
    else:
        merged = ours
    remote.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def restore(remote: str = "origin", branch: str = DEFAULT_BRANCH) -> int:
    """Copy state files from the state branch into ``state/`` (merging with local files)."""
    if not _remote_has_branch(remote, branch):
        print(f"No '{branch}' branch yet; starting with empty automation state.")
        return 0
    with tempfile.TemporaryDirectory(prefix="ai-studio-state-") as tmp:
        _git("fetch", "--depth", "1", remote, f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}")
        archive = Path(tmp) / "state.tar"
        _git("archive", "--format=tar", "-o", str(archive), f"refs/remotes/{remote}/{branch}")
        extracted = Path(tmp) / "tree"
        extracted.mkdir()
        shutil.unpack_archive(str(archive), str(extracted), format="tar")
        source = extracted / "state"
        target = _state_dir()
        count = 0
        for path in _state_files(source):
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                # Local file (e.g. a developer machine) is merged, never clobbered.
                scratch = Path(tmp) / "merge.json"
                shutil.copy2(path, scratch)
                _merge_file(destination, scratch)
                shutil.copy2(scratch, destination)
            else:
                shutil.copy2(path, destination)
            count += 1
    print(f"Restored {count} state file(s) from '{branch}'.")
    return count


def save(remote: str = "origin", branch: str = DEFAULT_BRANCH, message: str = "update automation state",
         attempts: int = 4) -> bool:
    """Commit ``state/`` to the state branch; merge and retry if the push is rejected."""
    files = _state_files(_state_dir())
    if not files:
        print("No automation state to persist.")
        return False
    last_error = ""
    for attempt in range(1, attempts + 1):
        with tempfile.TemporaryDirectory(prefix="ai-studio-state-") as tmp:
            work = Path(tmp) / "work"
            exists = _remote_has_branch(remote, branch)
            url = _git("remote", "get-url", remote).stdout.strip()
            if exists:
                _git("clone", "--quiet", "--depth", "1", "--branch", branch, "--single-branch",
                     url, str(work), cwd=tmp)
            else:
                work.mkdir()
                _git("init", "--quiet", cwd=work)
                _git("checkout", "--quiet", "--orphan", branch, cwd=work)
                _git("remote", "add", "origin", url, cwd=work)
                (work / "README.md").write_text(
                    "# Automation state\n\nMachine-written JSON only (duplicate ledger, news cache). "
                    "Do not merge this branch into `main`.\n", encoding="utf-8")
            _copy_auth_config(work)
            for path in files:
                destination = work / "state" / path.relative_to(_state_dir())
                destination.parent.mkdir(parents=True, exist_ok=True)
                _merge_file(path, destination)
            _git("add", "-A", cwd=work)
            if _git("diff", "--cached", "--quiet", cwd=work, check=False).returncode == 0:
                print("Automation state is already up to date.")
                return False
            _git("-c", f"user.name={os.getenv('STATE_GIT_NAME', 'studio-bot')}",
                 "-c", f"user.email={os.getenv('STATE_GIT_EMAIL', 'studio-bot@users.noreply.github.com')}",
                 "commit", "--quiet", "-m", f"state: {message}"[:200], cwd=work)
            pushed = _git("push", "--quiet", "origin", f"HEAD:refs/heads/{branch}", cwd=work, check=False)
            if pushed.returncode == 0:
                print(f"Persisted {len(files)} state file(s) to '{branch}'.")
                return True
            last_error = (pushed.stderr or "").strip()[:300]
        print(f"State push attempt {attempt}/{attempts} was rejected; merging and retrying.")
        time.sleep(min(2 * attempt, 10))
    raise RuntimeError(f"Could not persist automation state after {attempts} attempts: {last_error}")


def _copy_auth_config(work: Path) -> None:
    """Reuse the checkout's HTTP auth header (actions/checkout) inside the temp clone."""
    result = _git("config", "--local", "--get-regexp", r"^http\..*\.extraheader$", check=False)
    for line in (result.stdout or "").splitlines():
        key, _, value = line.partition(" ")
        if key and value:
            _git("config", "--local", key, value, cwd=work)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=["restore", "save"])
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--message", default="update automation state")
    args = parser.parse_args(argv)
    try:
        if args.action == "restore":
            restore(args.remote, args.branch)
        else:
            save(args.remote, args.branch, args.message)
    except Exception as exc:
        print(f"::error::Automation state {args.action} failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
