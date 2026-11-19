"""Typed contracts shared by all three independent v2 pipelines."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def to_dict(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, list):
        return [to_dict(v) for v in value]
    if isinstance(value, dict):
        return {k: to_dict(v) for k, v in value.items()}
    return value


@dataclass
class Source:
    id: str
    title: str
    publisher: str
    url: str
    published_at: str
    reliability: str = "unverified"  # primary|official|reputable|independent|demo|unverified
    notes: str = ""


@dataclass
class Claim:
    id: str
    text: str
    status: str = "needs_verification"  # established|interpretation|allegation|speculation
    source_ids: list[str] = field(default_factory=list)
    evidence: str = ""
    demonstration_only: bool = False


@dataclass
class Disagreement:
    claim_id: str
    description: str
    source_ids: list[str] = field(default_factory=list)
    resolution: str = ""


@dataclass
class ResearchDossier:
    topic: str
    content_type: str
    thesis: str
    sources: list[Source] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    disagreements: list[Disagreement] = field(default_factory=list)
    rejected_reason: str = ""
    demo_mode: bool = True


@dataclass
class VisualSpec:
    description: str
    kind: str = "generated"  # archive|map|diagram|animation|news_graphic|illustrative|generated
    source_url: str = ""
    rights: str = "synthetic demonstration graphic"
    duration: float = 6.0
    transition: str = "fade"
    camera_movement: str = "slow push-in"
    on_screen_text: str = ""
    asset_requirements: str = ""
    claim_ids: list[str] = field(default_factory=list)


@dataclass
class Scene:
    id: str
    narration: str
    visual: VisualSpec
    start: float = 0.0
    end: float = 0.0
    subtitle: str = ""
    motion: str = ""
    transition: str = ""
    audio: str = "narration + quiet bed"


@dataclass
class Script:
    content_type: str
    title: str
    central_question: str
    hook: str
    scenes: list[Scene]
    takeaway: str
    claim_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NarrationSegment:
    id: str
    text: str
    start: float
    end: float
    audio_path: str
    voice: str


@dataclass
class QualityReport:
    technical_quality: str = "FAIL"
    visual_quality: str = "FAIL"
    audio_quality: str = "FAIL"
    subtitle_quality: str = "FAIL"
    research_quality: str = "FAIL"
    copyright_compliance: str = "FAIL"
    upload_eligibility: str = "FAIL"
    checks: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    FIELD_NAMES = ("technical_quality", "visual_quality", "audio_quality", "subtitle_quality",
                   "research_quality", "copyright_compliance", "upload_eligibility")

    def decision(self) -> dict:
        return {name: getattr(self, name) for name in self.FIELD_NAMES}

    def finalize(self) -> "QualityReport":
        # The decision field is derived from the six substantive checks, not from
        # its own previous value.
        critical_names = [name for name in self.FIELD_NAMES if name != "upload_eligibility"]
        critical = [getattr(self, name) for name in critical_names]
        self.upload_eligibility = "PASS" if all(v == "PASS" for v in critical) else "FAIL"
        return self


@dataclass
class UploadResult:
    state: str  # SUCCESS|FAILED|SKIPPED|QUALITY_REJECTED|UPLOAD_FAILED|UPLOADED_PRIVATE
    video_id: str = ""
    privacy: str = ""
    uploaded_at: str = ""
    marker: str = ""
    content_hash: str = ""
    version: str = ""
    reused_existing_upload: bool = False
    error: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
