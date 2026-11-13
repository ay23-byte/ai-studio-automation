"""One bounded, atomic file download for every asset provider.

``requests`` timeouts apply to each read, not to the whole transfer, so a server
that sends one byte every minute never times out, and nothing stopped a response
from filling the disk. Each provider also wrote straight to the final path, so a
build killed mid-download left a truncated file where the next attempt expected
a finished one.

``fetch`` adds a total time limit, a size limit, a length check against
``Content-Length`` and a ``.part`` file that is renamed only when complete.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

CHUNK = 1024 * 1024


class DownloadError(RuntimeError):
    """The file could not be downloaded completely within the limits."""


def _limit(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except ValueError:
        value = float(default)
    return value if value > 0 else float("inf")


def max_bytes(kind: str = "video") -> float:
    """Largest accepted file: ``MAX_VIDEO_DOWNLOAD_MB`` (600) or ``MAX_IMAGE_DOWNLOAD_MB`` (80)."""
    if kind == "image":
        return _limit("MAX_IMAGE_DOWNLOAD_MB", 80) * 1024 * 1024
    return _limit("MAX_VIDEO_DOWNLOAD_MB", 600) * 1024 * 1024


def max_seconds() -> float:
    """Longest accepted transfer: ``MAX_DOWNLOAD_SECONDS`` (600)."""
    return _limit("MAX_DOWNLOAD_SECONDS", 600)


def fetch(url, path, *, kind: str = "video", headers: dict | None = None, timeout=(20, 90)) -> Path:
    """Download ``url`` to ``path``; raise :class:`DownloadError` and leave nothing behind on failure."""
    path = Path(path)
    part = path.with_name(path.name + ".part")
    size_limit, time_limit = max_bytes(kind), max_seconds()
    started, written = time.monotonic(), 0
    try:
        with requests.get(url, stream=True, timeout=timeout, headers=headers or {}) as response:
            response.raise_for_status()
            declared = None
            try:
                declared = int((getattr(response, "headers", None) or {}).get("Content-Length") or 0) or None
            except (TypeError, ValueError):
                declared = None
            if declared and declared > size_limit:
                raise DownloadError(f"file is {declared / 1048576:.0f} MB, above the "
                                    f"{size_limit / 1048576:.0f} MB limit")
            encoded = bool((getattr(response, "headers", None) or {}).get("Content-Encoding"))
            with part.open("wb") as target:
                for chunk in response.iter_content(CHUNK):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > size_limit:
                        raise DownloadError(f"download passed the {size_limit / 1048576:.0f} MB limit")
                    if time.monotonic() - started > time_limit:
                        raise DownloadError(f"download did not finish within {time_limit:.0f} seconds")
                    target.write(chunk)
            if declared and not encoded and written != declared:
                raise DownloadError(f"connection closed after {written} of {declared} bytes")
        if written == 0:
            raise DownloadError("the server returned an empty file")
        os.replace(part, path)
        return path
    except DownloadError:
        part.unlink(missing_ok=True)
        raise
    except (requests.RequestException, OSError) as exc:
        part.unlink(missing_ok=True)
        raise DownloadError(str(exc)) from exc
