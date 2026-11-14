"""Sort a failure message into one category a person can act on.

A failed production run should say what kind of problem it was (a missing key, a provider
out of quota, too little footage, a rejected presenter clip) and what to do next, not only
that a step exited with a non-zero code. The category is derived from the recorded failure
text; rules are tried in order, most specific first.
"""
from __future__ import annotations

import re

CATEGORIES: dict[str, tuple[str, str]] = {
    "configuration": ("Configuration",
                      "Add or correct the named secret, variable or workflow input, then start a new run."),
    "authentication": ("Authentication",
                       "The provider rejected the credential. Check that the named key is valid and allowed for this use."),
    "provider_quota": ("Provider quota",
                       "A provider's free allowance is used up or it is rate limiting. Wait for the reset, then start a new run."),
    "network": ("Network", "A provider could not be reached. Start a new run; if it repeats, check the provider's status."),
    "research": ("Research", "The research did not meet the evidence rules. Choose a better-documented topic or add sources."),
    "asset_shortage": ("Asset shortage",
                       "Too few relevant, licensed visuals were found. See the footage report and the manual-import list."),
    "presenter": ("Presenter", "The presenter clip could not be rendered or failed its motion check. See the presenter report."),
    "subtitles": ("Subtitles", "The subtitles could not be produced in English or do not match the narration."),
    "audio": ("Audio", "The narration or mix failed its checks. See the audio quality report."),
    "licensing": ("Licensing", "A visual has no usable licence evidence. Replace it or record the licence."),
    "editorial_rejection": ("Editorial rejection", "A reviewer or the release gate rejected the video. See the review decisions."),
    "rendering": ("Rendering", "FFmpeg or the editor failed. See the failing stage in the run report."),
    "unknown": ("Unclassified", "Read the failure text and the failing step in the run log."),
}

# (category, pattern). Order matters: a rate-limited provider also mentions HTTP, a missing
# key also mentions the provider, so the narrower rule comes first.
_RULES: list[tuple[str, str]] = [
    ("editorial_rejection", r"editorial|reviewer|review decision|rejected by (the )?review|release gate|not approved|approval"),
    ("presenter", r"presenter|talking avatar|3d avatar|avatar clip|sadtalker|lip-?sync|mouth moves"),
    ("subtitles", r"subtitle|caption|\bsrt\b|devanagari"),
    ("licensing", r"licen[cs]e|rights evidence|attribution|copyright|rights review"),
    ("configuration", r"is not set|not configured|missing (secret|variable|key|input)|must be one of|"
                      r"(?-i:[A-Z][A-Z0-9]+_[A-Z0-9_]+) (is|are) (required|missing)|"
                      r"is not installed|npm ci|no such file|not found on path|invalid (input|option|value)"),
    ("asset_shortage", r"fallback card|no (relevant |usable )?(footage|clip|visual|image)|footage shortage|"
                       r"asset shortage|visuals? (are|were) (missing|fallback)|missing visual|not enough (footage|visuals)|"
                       r"visual coverage"),
    ("research", r"research|source(s)? (were|was)? ?(found|required|missing)|claim|dossier|fact-?check|publisher domain|evidence"),
    ("provider_quota", r"\b429\b|quota|rate.?limit|too many requests|resource.?exhausted|retry-after|daily limit|credits"),
    ("authentication", r"\b401\b|\b403\b|unauthori[sz]ed|forbidden|invalid api key|api key (is )?(invalid|rejected)|"
                       r"permission denied|not accessible by integration|access denied"),
    ("network", r"timed? ?out|timeout|connection (reset|refused|aborted|error)|connecterror|ssl|dns|name resolution|"
                r"unreachable|\b50[0234]\b|network"),
    ("audio", r"audio|narration|voice|tts|loudness|lufs|clipping|silence|true peak"),
    ("rendering", r"ffmpeg|ffprobe|render|encode|encoder|codec|filter|mux|frame rate|resolution|black frame|frozen"),
]
_COMPILED = [(category, re.compile(pattern, re.I)) for category, pattern in _RULES]


def classify_failure(text) -> dict[str, str]:
    """Return {"category", "label", "action"} for a failure message ("unknown" if none fits)."""
    message = str(text or "")
    category = "unknown"
    if message.strip():
        for name, pattern in _COMPILED:
            if pattern.search(message):
                category = name
                break
    label, action = CATEGORIES[category]
    return {"category": category, "label": label, "action": action}
