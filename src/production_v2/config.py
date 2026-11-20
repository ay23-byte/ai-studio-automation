"""Configuration with safe defaults.

The v2 stack is opt-in.  It cannot publish publicly, cannot schedule a video,
and never selects a paid provider.  All knobs are read from environment
variables with conservative defaults.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Hard constants: these are safety properties, not user preferences.
PRIVACY_STATUS = "private"
CHANNEL_HANDLE = "@engineeringphysics-n5g"
ALLOWED_CONTENT_TYPES = ("documentary", "news_short", "physics_short")
ALLOWED_PROVIDERS = ("mock", "free")


@dataclass(frozen=True)
class ContentRules:
    width: int
    height: int
    fps: int
    min_duration: float
    max_duration: float
    min_sources: int
    subtitle_max_words: int
    subtitle_max_chars: int
    static_frame_limit: float = 0.92

    @property
    def aspect(self) -> str:
        return "16:9" if self.width > self.height else "9:16"


CONTENT_RULES: dict[str, ContentRules] = {
    "documentary": ContentRules(1920, 1080, 30, 30, 1800, 3, 9, 52, 0.90),
    "news_short": ContentRules(1080, 1920, 30, 25, 60, 2, 7, 42, 0.86),
    "physics_short": ContentRules(1080, 1920, 30, 25, 60, 1, 7, 42, 0.86),
}


@dataclass
class V2Config:
    enabled: bool = False
    demo_mode: bool = True
    provider: str = "mock"
    upload_enabled: bool = False
    language: str = "english"
    subtitle_language: str = "english"
    output_dir: Path = field(default_factory=lambda: ROOT / "build" / "v2")
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    tts_voice: str = "en-IN-PrabhatNeural"
    pronunciation_map: dict[str, str] = field(default_factory=dict)
    target_lufs: float = -16.0
    true_peak_dbfs: float = -1.5
    visual_manifest: str = ""
    failure_webhook: str = ""
    request_timeout_seconds: float = 20.0
    ffmpeg_timeout_seconds: float = 300.0
    rules: dict[str, ContentRules] = field(default_factory=lambda: dict(CONTENT_RULES))

    @classmethod
    def from_env(cls, env: dict | None = None) -> "V2Config":
        e = dict(os.environ if env is None else env)
        raw_map = e.get("PV2_PRONUNCIATION_MAP", "").strip()
        try:
            pronunciation = json.loads(raw_map) if raw_map else {}
        except ValueError:
            pronunciation = {}
        return cls(
            enabled=e.get("PV2_ENABLED", "0").strip().lower() in {"1", "true", "yes"},
            demo_mode=e.get("PV2_DEMO_MODE", "1").strip().lower() in {"1", "true", "yes"},
            provider=(e.get("PV2_PROVIDER", "mock") or "mock").strip().lower(),
            upload_enabled=e.get("PV2_UPLOAD", "0").strip().lower() in {"1", "true", "yes"},
            language=(e.get("PV2_LANGUAGE", "english") or "english").strip().lower(),
            subtitle_language=(e.get("PV2_SUBTITLE_LANGUAGE", "english") or "english").strip().lower(),
            output_dir=Path(e.get("PV2_OUTPUT_DIR") or (ROOT / "build" / "v2")),
            ffmpeg_binary=e.get("FFMPEG_BINARY", "ffmpeg"),
            ffprobe_binary=e.get("FFPROBE_BINARY", "ffprobe"),
            tts_voice=e.get("PV2_TTS_VOICE", "en-IN-PrabhatNeural"),
            pronunciation_map=pronunciation if isinstance(pronunciation, dict) else {},
            visual_manifest=e.get("PV2_VISUAL_MANIFEST", ""),
            failure_webhook=e.get("PV2_FAILURE_WEBHOOK", ""),
        )

    def assert_safe(self) -> None:
        """Fail closed on any configuration that could violate the brief."""
        if self.provider not in ALLOWED_PROVIDERS:
            raise ValueError(f"provider {self.provider!r} is not in the free/mock allow-list")
        if self.subtitle_language != "english":
            raise ValueError("v2 English production requires English subtitles")
        if self.language not in {"english", "hinglish", "hindi"}:
            raise ValueError(f"unsupported narration language {self.language!r}")
        for content_type in ALLOWED_CONTENT_TYPES:
            if content_type not in self.rules:
                raise ValueError(f"missing content rules for {content_type}")
        if PRIVACY_STATUS != "private":
            raise ValueError("v2 privacy constant must remain private")

    def rules_for(self, content_type: str) -> ContentRules:
        if content_type not in self.rules:
            raise ValueError(f"unknown content type {content_type!r}")
        return self.rules[content_type]
