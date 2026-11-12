"""Shared Gemini policy: model chain, failure classification, backoff, usage log.

Root cause addressed
--------------------
Gemini is called from three places (``core.script_engine``, ``core.news_shorts_writer``
and ``core.visual_semantics``).  Each had grown its own answers to the same
questions, and they disagreed:

* the default fallback models were hard-coded twice with different lists;
* an invalid API key was indistinguishable from any other rejected request, so
  one caller tried every configured model with the same dead key;
* one caller waited fixed 10/30/60 s delays with no jitter, so parallel runs
  retried in lock-step;
* a non-JSON body from the API escaped as a raw decoder exception;
* nothing recorded which model finally served a request.

This module holds the policy once.  The callers keep their own control flow and
prompts; they ask this module *which models*, *what kind of failure*, *how long
to wait* and *where to record the outcome*.

No credential is ever accepted, stored or logged here.
"""
from __future__ import annotations

import json
import os
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# The only place default model ids are written.  They are defaults, not
# assumptions: every deployment can override them without a code change, and an
# id the API no longer serves is skipped (HTTP 404) rather than fatal.
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_FALLBACK_MODELS = (
    "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
)

# Failure kinds.  Callers branch on these instead of on raw status codes.
AUTH = "auth"                        # invalid/unauthorised key: same for every model, never retry
MODEL_NOT_FOUND = "model_not_found"  # unknown/retired id: skip to the next model, never retry
RATE_LIMITED = "rate_limited"        # 429: honour Retry-After, bounded retries, then next model
SERVER = "server"                    # 500/502/503/504: bounded backoff, then next model
TRANSIENT = "transient"              # 408/409/425: bounded backoff
BAD_REQUEST = "bad_request"          # other 4xx: the request itself is wrong, do not retry
NETWORK = "network"                  # timeout / connection failure: bounded backoff
MALFORMED = "malformed"              # HTTP 200 without usable content

RETRYABLE_KINDS = frozenset({RATE_LIMITED, SERVER, TRANSIENT, NETWORK})

_MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")
_AUTH_MARKERS = ("api_key_invalid", "api key not valid", "api key expired", "permission_denied",
                 "unauthenticated", "invalid api key", "api_key_service_blocked")


class GeminiConfigError(ValueError):
    """The configured model list is unusable (bad id, nothing configured)."""


class GeminiCredentialError(RuntimeError):
    """The API key was rejected.  Never retried and never tried on another model."""


def normalise_model_id(value) -> str:
    """Return a clean model id; accepts the ``models/<id>`` form the API lists."""
    model = str(value or "").strip()
    if model.lower().startswith("models/"):
        model = model[len("models/"):]
    return model


def validate_model_id(value) -> str:
    """Return the normalised id or raise :class:`GeminiConfigError`.

    The id is interpolated into a request URL, so anything that is not a plain
    model name (spaces, slashes, query strings, quotes) is rejected up front with
    a message that names the setting to fix.
    """
    model = normalise_model_id(value)
    if not model:
        raise GeminiConfigError("A Gemini model id is empty. Set GEMINI_MODEL.")
    if not _MODEL_ID.match(model):
        raise GeminiConfigError(
            f"Invalid Gemini model id {str(value)!r}. Use a plain id such as "
            f"{DEFAULT_MODEL!r} in GEMINI_MODEL / GEMINI_FALLBACK_MODELS (comma-separated).")
    return model


def _split(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [part for part in raw.split(",")]
    return [str(part) for part in raw]


def configured_fallbacks(environ=None) -> list[str]:
    """Fallback ids from ``GEMINI_FALLBACK_MODELS`` (or legacy ``GEMINI_FALLBACK_MODEL``).

    Unset or empty -> the default chain; ``none`` -> no fallbacks.
    """
    env = os.environ if environ is None else environ
    raw = env.get("GEMINI_FALLBACK_MODELS")
    if raw is None or not str(raw).strip():
        raw = env.get("GEMINI_FALLBACK_MODEL")
    # An unset GitHub Actions variable reaches the process as an empty string.
    # Treating that as "no fallbacks" would silently remove the safety net, so
    # empty means "use the defaults"; write ``none`` to disable fallbacks.
    if raw is None or not str(raw).strip():
        return list(DEFAULT_FALLBACK_MODELS)
    if str(raw).strip().lower() in {"none", "off", "disabled"}:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def model_chain(primary=None, fallbacks=None, *, strict: bool = True, environ=None) -> list[str]:
    """Ordered, validated, de-duplicated models to try: primary first.

    ``primary`` defaults to ``GEMINI_MODEL``; ``fallbacks`` to the configured
    fallback list.  With ``strict=False`` invalid ids are dropped instead of
    raising (used by diagnostics, which report them separately).
    """
    env = os.environ if environ is None else environ
    if primary is None:
        primary = env.get("GEMINI_MODEL") or DEFAULT_MODEL
    if fallbacks is None:
        fallbacks = configured_fallbacks(env)
    chain: list[str] = []
    for raw in [primary, *_split(fallbacks)]:
        if not str(raw or "").strip():
            continue
        try:
            model = validate_model_id(raw)
        except GeminiConfigError:
            if strict:
                raise
            continue
        if model not in chain:
            chain.append(model)
    if not chain:
        raise GeminiConfigError("No Gemini model is configured. Set GEMINI_MODEL.")
    return chain


def invalid_model_ids(primary=None, fallbacks=None, environ=None) -> list[str]:
    """Configured ids that fail validation (for ``run.py doctor``)."""
    env = os.environ if environ is None else environ
    if primary is None:
        primary = env.get("GEMINI_MODEL") or DEFAULT_MODEL
    if fallbacks is None:
        fallbacks = configured_fallbacks(env)
    bad = []
    for raw in [primary, *_split(fallbacks)]:
        if not str(raw or "").strip():
            continue
        try:
            validate_model_id(raw)
        except GeminiConfigError:
            bad.append(str(raw).strip())
    return bad


def classify(status_code=None, body: str = "", network_error: bool = False) -> str:
    """Map an HTTP outcome to one failure kind.

    Google reports an invalid key as HTTP 400 with ``API_KEY_INVALID`` in the
    body, not as 401, so the body is inspected before the generic 4xx rule.
    """
    if network_error or status_code is None:
        return NETWORK
    status = int(status_code)
    text = str(body or "").lower()
    if status in (401, 403) or (status == 400 and any(marker in text for marker in _AUTH_MARKERS)):
        return AUTH
    if status == 404:
        return MODEL_NOT_FOUND
    if status == 429:
        return RATE_LIMITED
    if status in (500, 502, 503, 504):
        return SERVER
    if status in (408, 409, 425):
        return TRANSIENT
    if 400 <= status < 500:
        return BAD_REQUEST
    if status >= 500:
        return SERVER
    return MALFORMED


def is_retryable(kind: str) -> bool:
    return kind in RETRYABLE_KINDS


def describe(kind: str, model: str = "") -> str:
    """One actionable sentence per failure kind; never includes a credential."""
    target = f" for model {model}" if model else ""
    return {
        AUTH: "Gemini rejected the API key (invalid, expired or not authorised for this API). "
              "Create a new key in Google AI Studio and update the GEMINI_API_KEY secret; "
              "retrying or switching model cannot fix this.",
        MODEL_NOT_FOUND: f"Gemini does not serve this model id{target}. Update GEMINI_MODEL / "
                         "GEMINI_FALLBACK_MODELS; the next configured model is tried.",
        RATE_LIMITED: f"Gemini rate limit or quota reached{target}. The request is retried after "
                      "the delay Google asks for, then the next model is tried.",
        SERVER: f"Gemini is temporarily unavailable{target} (server error).",
        TRANSIENT: f"Gemini returned a transient error{target}.",
        BAD_REQUEST: f"Gemini rejected the request{target} as invalid; it is not retried.",
        NETWORK: f"Network failure reaching Gemini{target}.",
        MALFORMED: f"Gemini returned no usable content{target}.",
    }.get(kind, f"Gemini request failed{target}.")


def parse_retry_after(value, now: datetime | None = None) -> float | None:
    """Seconds requested by a ``Retry-After`` header (delta-seconds or HTTP-date)."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0.0, (when - current).total_seconds())


def backoff_delay(attempt: int, *, retry_after=None, base: float = 10.0, factor: float = 2.0,
                  cap: float = 60.0, jitter: float = 1.0, minimum: float = 1.0,
                  schedule=None) -> float:
    """Bounded wait before the next attempt.

    * ``Retry-After`` wins when present, clamped to ``[minimum, cap]``: the
      server knows its own recovery time, but a hostile or broken value must not
      stall a workflow for an hour.
    * Otherwise ``schedule[attempt]`` (if given) or ``base * factor**attempt``.
    * Up to ``jitter`` seconds are added so concurrent jobs do not retry together;
      the result never exceeds ``cap``.
    """
    requested = parse_retry_after(retry_after)
    if requested is not None:
        delay = requested
    elif schedule:
        delay = float(schedule[min(int(attempt), len(schedule) - 1)])
    else:
        delay = float(base) * (float(factor) ** max(int(attempt), 0))
    delay = max(float(minimum), min(float(cap), delay))
    if jitter and jitter > 0:
        delay += random.uniform(0.0, float(jitter))
    # ``cap`` is a hard ceiling: jitter never pushes a wait past it.
    return min(float(cap), delay)


def response_text(data) -> str:
    """Concatenated text of the first candidate, or ``""`` when there is none."""
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        return ""
    if not isinstance(parts, list):
        return ""
    return "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))


def finish_reason(data) -> str:
    try:
        return str(data["candidates"][0].get("finishReason") or "").upper()
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


# ---------------------------------------------------------------------------
# Usage record: which model served which request.  No prompt, no key.
# ---------------------------------------------------------------------------
_LAST_USAGE: dict = {}


def _log_path():
    try:
        from core import config as cfg
        # Unit tests must not write into the operator's output folder.
        if cfg._running_under_test_runner() and not os.getenv("AI_STUDIO_LOG_DIR"):
            return None
        return cfg.LOG_DIR / "llm_usage.jsonl"
    except Exception:
        return None


def record_usage(purpose: str, *, model: str | None, outcome: str, provider: str = "gemini",
                 attempted=None, failures=None, seconds: float | None = None) -> dict:
    """Remember and append one usage record; never raises into the pipeline."""
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose": str(purpose or "unspecified"),
        "provider": provider,
        "model": model,
        "outcome": outcome,                       # "ok" | failure kind
        "attempted_models": list(attempted or ([model] if model else [])),
        "failures": [str(item)[:300] for item in (failures or [])][:12],
    }
    if seconds is not None:
        entry["seconds"] = round(float(seconds), 2)
    _LAST_USAGE[entry["purpose"]] = entry
    path = _log_path()
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return entry


def last_usage(purpose: str | None = None) -> dict:
    """Most recent usage record for ``purpose`` (or the latest of any purpose)."""
    if purpose is not None:
        return dict(_LAST_USAGE.get(str(purpose)) or {})
    if not _LAST_USAGE:
        return {}
    return dict(max(_LAST_USAGE.values(), key=lambda item: item.get("at", "")))


class Stopwatch:
    """Tiny helper so callers can report elapsed seconds without importing time."""

    def __init__(self):
        self.started = time.monotonic()

    def seconds(self) -> float:
        return time.monotonic() - self.started
