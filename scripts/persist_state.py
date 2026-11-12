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


GIT_TIMEOUT_SECONDS = 180
NETWORK_ATTEMPTS = 3


def _git(*args, cwd=None, check=True, capture=True, env=None):
    """Run git with a time limit.  ``env`` adds variables (used for credentials,
    which must never be placed on the command line)."""
    environment = None
    if env:
        environment = dict(os.environ)
        environment.update(env)
    try:
        result = subprocess.run(["git", *args], cwd=str(cwd or ROOT), text=True,
                                capture_output=capture, env=environment,
                                timeout=GIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        if check:
            raise RuntimeError(f"git {' '.join(args[:2])} timed out after {GIT_TIMEOUT_SECONDS}s") from exc
        return subprocess.CompletedProcess(["git", *args], 124, "", "timed out")
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {_scrub(result.stderr)[:400]}")
    return result


def _scrub(text) -> str:
    """Remove anything credential-shaped from git output before it is printed."""
    import re
    text = str(text or "").strip()
    text = re.sub(r"(?i)(authorization:\s*)(basic|bearer)\s+\S+", r"\1\2 [REDACTED]", text)
    text = re.sub(r"(https?://)[^/@\s]+@", r"\1[REDACTED]@", text)
    return re.sub(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b", "[REDACTED]", text)


def _config_entries(pattern: str) -> list[tuple[str, str]]:
    """``(key, value)`` pairs from the checkout's local git config matching ``pattern``."""
    result = _git("config", "--local", "-z", "--get-regexp", pattern, check=False)
    entries = []
    for record in (result.stdout or "").split("\0"):
        key, _, value = record.partition("\n")
        if key.strip() and value:
            entries.append((key.strip(), value))
    return entries


def _auth_env(remote: str = "origin") -> dict:
    """Environment that gives git in the temporary clone the same access as the checkout.

    The temporary clone is a different repository, so it does not inherit the
    credential that ``actions/checkout`` configured.  Three layouts are handled:

    1. an explicit token in ``STATE_PUSH_TOKEN`` / ``GITHUB_TOKEN`` / ``GH_TOKEN``;
    2. ``actions/checkout`` v4 and v5, which store an ``http.<host>/.extraheader``
       entry in ``.git/config``;
    3. ``actions/checkout`` v6 and later, which keep the header in a separate file
       under ``$RUNNER_TEMP`` and reference it with ``includeIf.gitdir:<checkout>``.
       That condition does not match the temporary clone, so the file is included
       directly.

    Values are passed through ``GIT_CONFIG_COUNT``/``GIT_CONFIG_KEY_n``/
    ``GIT_CONFIG_VALUE_n`` so that no credential appears in a process argument
    list or is written into the temporary clone.
    """
    import base64
    from urllib.parse import urlsplit
    pairs: list[tuple[str, str]] = []
    token = (os.getenv("STATE_PUSH_TOKEN") or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
    url = (_git("remote", "get-url", remote, check=False).stdout or "").strip()
    parts = urlsplit(url) if url.startswith(("http://", "https://")) else None
    if token and parts and parts.hostname:
        origin = f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        pairs.append((f"http.{origin}/.extraheader", f"AUTHORIZATION: basic {basic}"))
    else:
        pairs.extend(_config_entries(r"^http\..*\.extraheader$"))
        if not pairs:
            seen = set()
            for _key, path in _config_entries(r"^includeif\..*\.path$"):
                if path not in seen and Path(path).is_file():
                    seen.add(path)
                    pairs.append(("include.path", path))
    if not pairs:
        return {}
    env = {"GIT_CONFIG_COUNT": str(len(pairs))}
    for index, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def _state_dir() -> Path:
    return Path(os.getenv("AI_STUDIO_STATE_DIR") or ROOT / "state")


def _state_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.rglob("*")
                  if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES
                  and not path.name.endswith(".tmp") and path.stat().st_size <= MAX_FILE_BYTES)


def _remote_has_branch(remote: str, branch: str, attempts: int = NETWORK_ATTEMPTS) -> bool:
    """True/False only when the remote actually answered.

    ``git ls-remote --exit-code`` returns 2 for "no such branch".  Any other
    non-zero code is a network or authentication problem; treating that as "no
    branch yet" would start the run with an empty duplicate ledger and rebuild
    (or re-upload) subjects that were already published, so it is an error.
    """
    detail = ""
    for attempt in range(1, attempts + 1):
        result = _git("ls-remote", "--exit-code", "--heads", remote, branch, check=False)
        if result.returncode == 0:
            return True
        if result.returncode == 2:
            return False
        detail = _scrub(result.stderr)[:300]
        if attempt < attempts:
            time.sleep(min(3 * attempt, 10))
    raise RuntimeError(f"Could not query '{remote}' for branch '{branch}' after {attempts} attempts: {detail}")


def _git_retry(*args, attempts: int = NETWORK_ATTEMPTS, **kwargs):
    """Run a network git command, retrying transient failures."""
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return _git(*args, **kwargs)
        except RuntimeError as exc:
            last = exc
            if attempt < attempts:
                time.sleep(min(3 * attempt, 10))
    raise last


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
        _git_retry("fetch", "--depth", "1", remote, f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}")
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
    auth = _auth_env(remote)
    for attempt in range(1, attempts + 1):
        with tempfile.TemporaryDirectory(prefix="ai-studio-state-") as tmp:
            work = Path(tmp) / "work"
            exists = _remote_has_branch(remote, branch)
            url = _git("remote", "get-url", remote).stdout.strip()
            if exists:
                _git_retry("clone", "--quiet", "--depth", "1", "--branch", branch, "--single-branch",
                           url, str(work), cwd=tmp, env=auth)
            else:
                work.mkdir()
                _git("init", "--quiet", cwd=work)
                _git("checkout", "--quiet", "--orphan", branch, cwd=work)
                _git("remote", "add", "origin", url, cwd=work)
                (work / "README.md").write_text(
                    "# Automation state\n\nMachine-written JSON only (duplicate ledger, news cache). "
                    "Do not merge this branch into `main`.\n", encoding="utf-8")
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
            pushed = _git("push", "--quiet", "origin", f"HEAD:refs/heads/{branch}", cwd=work, check=False,
                          env=auth)
            if pushed.returncode == 0:
                print(f"Persisted {len(files)} state file(s) to '{branch}'.")
                return True
            last_error = _scrub(pushed.stderr)[:300]
        if attempt < attempts:
            print(f"State push attempt {attempt}/{attempts} was rejected; merging and retrying.")
            time.sleep(min(2 * attempt, 10))
    hint = ""
    if any(word in last_error.lower() for word in ("403", "denied", "authentication", "could not read username")):
        hint = (" The workflow needs 'permissions: contents: write' and a checkout that keeps its "
                "credentials (persist-credentials must not be false), or GITHUB_TOKEN in the step environment.")
    raise RuntimeError(f"Could not persist automation state after {attempts} attempts: {last_error}{hint}")


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
