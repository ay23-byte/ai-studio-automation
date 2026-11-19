"""Content-type-specific script construction and validation.

Narration is derived only from dossier claims and thesis. The engine never
invents a quotation, statistic, event or source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import ContentRules, V2Config
from .models import Claim, ResearchDossier, Scene, Script, VisualSpec, to_dict

DEVANAGARI = re.compile(r"[\u0900-\u097f]")
ATTRIBUTION = ("according to", "reported by", "data from", "official statement", "published by")


@dataclass
class ScriptAssessment:
    accepted: bool
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return to_dict(self)


def _claim_text(claim: Claim) -> str:
    text = claim.text.strip()
    if claim.status == "allegation" and not re.search(r"alleg|report|unverified", text, re.I):
        return "An unverified allegation says that " + text[0].lower() + text[1:]
    if claim.status == "speculation" and not re.search(r"may|might|unclear", text, re.I):
        return "It remains unclear whether " + text[0].lower() + text[1:]
    return text


def _source_for(claim: Claim, dossier: ResearchDossier):
    for sid in claim.source_ids:
        for source in dossier.sources:
            if source.id == sid:
                return source
    return None


def _narration_for(claim: Claim, dossier: ResearchDossier, style: str) -> str:
    text = _claim_text(claim)
    if style == "news":
        source = _source_for(claim, dossier)
        if source and claim.status == "established":
            return f"According to {source.publisher}, {text[0].lower() + text[1:]}"
    if style == "documentary" and claim.status == "established":
        source = _source_for(claim, dossier)
        if source:
            return f"The record from {source.publisher} shows that {text[0].lower() + text[1:]}"
    return text


def _visual(claim: Claim, style: str, index: int) -> VisualSpec:
    if style == "news":
        return VisualSpec(
            description=f"Labelled news graphic and map for: {claim.text[:90]}",
            kind="news_graphic", duration=5.5, transition="cut" if index % 2 else "fade",
            camera_movement="subtle horizontal drift", on_screen_text=claim.id.upper(),
            asset_requirements="attribution label and ILLUSTRATIVE marker",
            claim_ids=[claim.id])
    if style == "physics":
        return VisualSpec(
            description=f"Animated scientific diagram for: {claim.text[:90]}",
            kind="animation", duration=5.0, transition="crossfade",
            camera_movement="vector motion with guided emphasis", on_screen_text=claim.id.upper(),
            asset_requirements="correct labels, units and assumptions",
            claim_ids=[claim.id])
    return VisualSpec(
        description=f"Timeline/map/data panel grounded in: {claim.text[:90]}",
        kind="diagram" if index % 2 else "map", duration=7.0, transition="dissolve",
        camera_movement="slow push-in", on_screen_text=f"EVIDENCE {index:02d}",
        asset_requirements="source label and dated evidence panel", claim_ids=[claim.id])


def _build_script(dossier: ResearchDossier, rules: ContentRules, style: str,
                  title_override: str = "") -> Script:
    claims = [c for c in dossier.claims if c.status != "needs_verification"] or dossier.claims
    if not claims:
        raise ValueError("dossier has no claims; refusing to fabricate a script")
    scenes: list[Scene] = []
    for index, claim in enumerate(claims):
        narration = _narration_for(claim, dossier, style)
        visual = _visual(claim, style, index)
        scenes.append(Scene(
            id=f"scene_{index + 1:02d}", narration=narration, visual=visual,
            subtitle=narration, motion=visual.camera_movement, transition=visual.transition))
    if style == "news":
        hook = "Here is what is confirmed, and what is still developing."
        question = "What happened, why does it matter, and what comes next?"
        takeaway = "Follow the sources in the description before sharing this story."
    elif style == "physics":
        hook = "Here is the idea that makes this concept click."
        question = "What is really happening here?"
        takeaway = "One concept, one visual, one clear takeaway."
    else:
        hook = f"One question sits at the centre of this story: {dossier.thesis}"
        question = dossier.thesis
        takeaway = "The evidence, not the spectacle, is the story."

    intro = Scene(
        id="scene_hook", narration=hook,
        visual=VisualSpec(description=f"Opening title and central question: {question[:90]}",
                          kind="illustrative", duration=8.0, transition="fade in",
                          camera_movement="slow push-in", on_screen_text="OPENING QUESTION",
                          asset_requirements=" clearly labelled opening graphic"),
        subtitle=hook, motion="slow push-in", transition="fade in")
    outro = Scene(
        id="scene_takeaway", narration=takeaway,
        visual=VisualSpec(description=f"Closing takeaway panel: {takeaway[:90]}",
                          kind="illustrative", duration=8.0, transition="fade out",
                          camera_movement="slow pull-back", on_screen_text="TAKEAWAY",
                          asset_requirements="source list in credits"),
        subtitle=takeaway, motion="slow pull-back", transition="fade out")
    scenes = [intro, *scenes, outro]
    return Script(
        content_type=dossier.content_type, title=title_override or dossier.topic,
        central_question=question, hook=hook, scenes=scenes, takeaway=takeaway,
        claim_ids=[c.id for c in claims],
        metadata={"demo_mode": dossier.demo_mode, "style": style,
                  "target_resolution": f"{rules.width}x{rules.height}"})


def build_script(dossier: ResearchDossier, config: V2Config, *, title: str = "") -> Script:
    style = {"documentary": "documentary", "news_short": "news", "physics_short": "physics"}.get(
        dossier.content_type, "documentary")
    return _build_script(dossier, config.rules_for(dossier.content_type), style, title)


def validate_script(script: Script, dossier: ResearchDossier, rules: ContentRules) -> ScriptAssessment:
    issues: list[str] = []
    warnings: list[str] = []
    if not script.scenes:
        issues.append("script has no scenes")
    total = sum(s.visual.duration for s in script.scenes)
    if not rules.min_duration <= total <= rules.max_duration:
        issues.append(f"planned duration {total:.1f}s is outside {rules.min_duration}-{rules.max_duration}s")

    claim_map = {c.id: c for c in dossier.claims}
    referenced = set()
    for scene in script.scenes:
        if not scene.narration.strip():
            issues.append(f"{scene.id}: empty narration")
        if not scene.visual.description.strip():
            issues.append(f"{scene.id}: no visual description")
        if scene.visual.kind not in {"archive", "map", "diagram", "animation",
                                     "news_graphic", "illustrative", "generated"}:
            issues.append(f"{scene.id}: unknown visual kind")
        if DEVANAGARI.search(scene.narration):
            issues.append(f"{scene.id}: non-English Devanagari text in English narration")
        for cid in scene.visual.claim_ids:
            claim = claim_map.get(cid)
            if claim is None:
                issues.append(f"{scene.id}: unknown claim {cid}")
                continue
            referenced.add(cid)
            if claim.status in {"allegation", "speculation"} and not re.search(
                r"alleg|report|unverified|unclear|may|might", scene.narration, re.I
            ):
                issues.append(f"{scene.id}: {claim.status} is narrated as confirmed fact")
        if scene.visual.kind in {"generated", "illustrative"} and not scene.visual.on_screen_text:
            warnings.append(f"{scene.id}: generated visual should be labelled on screen")

    if script.content_type == "news_short" and not any(
        marker in " ".join(s.narration.lower() for s in script.scenes) for marker in ATTRIBUTION
    ):
        issues.append("news script has no source attribution in narration")
    if dossier.claims and not referenced:
        issues.append("no scene maps to a research claim")
    return ScriptAssessment(not issues, issues, warnings)
