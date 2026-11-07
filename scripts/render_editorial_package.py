#!/usr/bin/env python3
"""Render a reviewed, repository-stored documentary script through the existing pipeline.

This entry point deliberately bypasses script generation: the supplied JSON is the
editorially prepared source of truth. It still uses the normal TTS, visual sourcing,
render, provenance, visual-review and quality-report stages.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.db import save_video
from core.pipeline import build_longform
from core.script_engine import validate_longform_storyboard


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a prepared documentary JSON package.")
    parser.add_argument("--script", required=True, help="Path to the prepared script JSON.")
    parser.add_argument("--voice-gender", choices=("male", "female"), default="female")
    parser.add_argument("--language", choices=("hindi", "hinglish", "english"), default="hinglish")
    args = parser.parse_args()

    script_path = Path(args.script).resolve()
    if not script_path.is_file():
        raise FileNotFoundError(f"Script JSON not found: {script_path}")
    script = json.loads(script_path.read_text(encoding="utf-8"))
    if not isinstance(script, dict):
        raise ValueError("The script JSON root must be an object.")
    validate_longform_storyboard(script)
    if not script.get("sources") or not script.get("research_sources"):
        raise ValueError("Prepared documentary must include sources and research_sources.")
    if not script.get("claims"):
        raise ValueError("Prepared documentary must include a traceable claims array.")

    title = str(script.get("title") or "Untitled documentary").strip()
    category = str(script.get("category") or "general").strip()
    job_id = save_video(title, "longform", args.language, category, script)
    print(f"Prepared editorial package: {script_path}")
    print(f"Research sources: {len(script['research_sources'])}; claims: {len(script['claims'])}")
    print(f"Starting render job #{job_id} with {args.voice_gender} narration.")
    output = build_longform(script, job_id, language=args.language, voice_gender=args.voice_gender)
    print(f"DOCUMENTARY_RENDER_COMPLETE job_id={job_id} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
