"""Prepare scene-relevant visual candidates for a reviewed news Short.

This stage never renders the final MP4 or uploads/publishes to YouTube.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def find_one(root: str | Path, filename: str) -> Path:
    matches = sorted(Path(root).rglob(filename))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {filename}; found {len(matches)}.")
    return matches[0]


def validate_draft(packet: dict, script: dict) -> None:
    if packet.get("review_status") != "approved":
        raise ValueError("Evidence packet must be approved before asset preparation.")
    sources = packet.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Evidence packet must contain sources.")
    if not isinstance(packet.get("verified_facts"), list) or not packet["verified_facts"]:
        raise ValueError("Evidence packet must contain verified facts.")
    if script.get("editorial_status") != "draft_requires_human_script_and_visual_review":
        raise ValueError("Expected the generated human-review script draft.")
    scenes = script.get("scenes")
    if not isinstance(scenes, list) or not 4 <= len(scenes) <= 6:
        raise ValueError("News Short must contain 4–6 scenes.")
    known = {str(item.get("source_id")) for item in sources if item.get("source_id")}
    for number, scene in enumerate(scenes, start=1):
        for key in ("say", "caption", "image"):
            if not str(scene.get(key) or "").strip():
                raise ValueError(f"Scene {number} is missing {key}.")
        refs = scene.get("source_ids")
        if not isinstance(refs, list) or not refs or not set(map(str, refs)).issubset(known):
            raise ValueError(f"Scene {number} must cite known source IDs.")


def is_himalayan_story(packet: dict, script: dict) -> bool:
    text = " ".join([
        str(packet.get("story_title", "")),
        str(script.get("title", "")),
        str(packet.get("story_id", "")),
    ]).lower()
    return "himalay" in text


def visual_plan(scene_number: int, scene_count: int, is_himalayan: bool) -> dict:
    """Return curated visual intent; never use generic 'research' filler for this story."""
    if not is_himalayan:
        return {}
    if scene_number == 1:
        return {"kind": "graphic", "graphic": "population_exposure"}
    if scene_number == 2:
        # Define HAND in the same scene as the Nepal exposure figures.
        return {"kind": "graphic", "graphic": "hand_explainer"}
    if scene_number == 3:
        return {"kind": "graphic", "graphic": "building_growth"}
    if scene_number == 4:
        return {"kind": "graphic", "graphic": "not_forecast"}
    if scene_number == scene_count:
        return {"kind": "graphic", "graphic": "outro"}
    return {"kind": "stock", "queries": [
        "Himalayan glacier river valley", "Himalaya mountain river"
    ]}


def _font(size: int):
    from PIL import ImageFont
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def create_story_graphic(path: str | Path, graphic: str) -> dict:
    """Create clean, legible, text-accurate graphics locally (not generative AI)."""
    from PIL import Image, ImageDraw

    width, height = 1080, 1920
    image = Image.new("RGB", (width, height), (12, 24, 39))
    draw = ImageDraw.Draw(image)
    # restrained decorative terrain-like lines; no invented map boundaries
    for y in range(1220, 1810, 95):
        draw.arc((-180, y - 130, 1260, y + 260), 195, 345, fill=(37, 65, 82), width=4)

    white = (245, 248, 250)
    muted = (178, 197, 210)
    accent = (100, 205, 190)
    large = _font(112)
    medium = _font(58)
    small = _font(38)
    x = 82

    if graphic == "population_exposure":
        draw.text((x, 190), "HIMALAYAN STUDY", font=medium, fill=accent)
        draw.text((x, 390), "293,800", font=_font(132), fill=white)
        draw.text((x, 570), "PEOPLE", font=large, fill=white)
        draw.rounded_rectangle((70, 850, 1010, 1190), radius=34, fill=(24, 47, 64), outline=(55, 87, 105), width=3)
        draw.text((120, 920), "18% OF STUDY-AREA", font=medium, fill=accent)
        draw.text((120, 1010), "CATCHMENT POPULATION", font=small, fill=white)
        draw.text((x, 1320), "WITHIN 0–50 m HAND IN 2026", font=small, fill=muted)
        draw.text((x, 1410), "MODELLED POTENTIAL EXPOSURE", font=small, fill=muted)
    elif graphic == "hand_explainer":
        draw.text((x, 170), "WHAT DOES HAND MEAN?", font=medium, fill=accent)
        # Keep long headings inside the 9:16 safe area.
        hand_title = _font(82)
        draw.text((x, 300), "VERTICAL HEIGHT", font=hand_title, fill=white)
        draw.text((x, 410), "ABOVE DRAINAGE", font=hand_title, fill=white)
        # Schematic terrain cross-section, not a geographic map.
        terrain = [(70, 1160), (220, 900), (390, 1040), (560, 760),
                   (730, 1010), (880, 900), (1010, 1130)]
        draw.line(terrain, fill=(100, 205, 190), width=18, joint="curve")
        draw.line((450, 1200, 630, 1200), fill=(105, 185, 235), width=18)
        draw.line((450, 1220, 630, 1220), fill=(105, 185, 235), width=5)
        draw.line((760, 1010, 760, 1195), fill=(255, 190, 115), width=9)
        draw.polygon([(760, 985), (740, 1025), (780, 1025)], fill=(255, 190, 115))
        draw.polygon([(760, 1220), (740, 1180), (780, 1180)], fill=(255, 190, 115))
        draw.text((785, 1060), "HEIGHT", font=small, fill=(255, 210, 155))
        draw.text((430, 1280), "NEAREST DRAINAGE", font=small, fill=(150, 210, 245))
        draw.rounded_rectangle((70, 1450, 1010, 1690), radius=28, fill=(24, 47, 64), outline=(55, 87, 105), width=3)
        draw.text((105, 1490), "0–50 m HAND = VERTICAL HEIGHT", font=_font(34), fill=white)
        draw.text((105, 1560), "NOT horizontal distance from a river", font=_font(32), fill=muted)
    elif graphic == "building_growth":
        draw.text((x, 190), "HIMALAYAN STUDY", font=medium, fill=accent)
        draw.text((x, 340), "BUILDING", font=large, fill=white)
        draw.text((x, 465), "FOOTPRINT AREA", font=medium, fill=white)
        draw.rounded_rectangle((70, 650, 1010, 1030), radius=34, fill=(24, 47, 64), outline=(55, 87, 105), width=3)
        draw.text((120, 710), "+46%", font=large, fill=accent)
        draw.text((120, 850), "within 0–50 m HAND", font=medium, fill=white)
        draw.rounded_rectangle((70, 1100, 1010, 1480), radius=34, fill=(24, 47, 64), outline=(55, 87, 105), width=3)
        draw.text((120, 1160), "+77%", font=large, fill=accent)
        draw.text((120, 1300), "within modelled GLOF extents", font=small, fill=white)
        draw.text((x, 1570), "2016–2023  •  AREA, NOT BUILDING COUNT", font=small, fill=muted)
    elif graphic == "nepal_breakdown":
        draw.text((x, 190), "NEPAL BREAKDOWN", font=medium, fill=accent)
        draw.text((x, 390), "MODELLED", font=large, fill=white)
        draw.text((x, 520), "POTENTIAL", font=large, fill=white)
        draw.text((x, 650), "EXPOSURE", font=large, fill=white)
        draw.rounded_rectangle((70, 850, 1010, 1190), radius=34, fill=(24, 47, 64), outline=(55, 87, 105), width=3)
        draw.text((120, 900), "108,500", font=large, fill=accent)
        draw.text((120, 1040), "within 0–50 m HAND", font=small, fill=white)
        draw.rounded_rectangle((70, 1260, 1010, 1530), radius=34, fill=(24, 47, 64), outline=(55, 87, 105), width=3)
        draw.text((120, 1290), "22,800", font=large, fill=accent)
        draw.text((120, 1430), "within 0–10 m HAND", font=small, fill=white)
        draw.text((x, 1660), "HAND = VERTICAL HEIGHT ABOVE NEAREST DRAINAGE", font=_font(30), fill=muted)
    elif graphic == "not_forecast":
        draw.text((x, 280), "IMPORTANT CONTEXT", font=medium, fill=accent)
        draw.text((x, 520), "MODELLED", font=large, fill=white)
        draw.text((x, 660), "POTENTIAL", font=large, fill=white)
        draw.text((x, 800), "EXPOSURE", font=large, fill=white)
        draw.line((x, 1010, 990, 1010), fill=accent, width=6)
        draw.text((x, 1110), "IS NOT A", font=medium, fill=muted)
        draw.text((x, 1220), "FLOOD", font=large, fill=(255, 190, 115))
        draw.text((x, 1360), "FORECAST", font=large, fill=(255, 190, 115))
        disclaimer = "The study does not prove construction caused flooding."
        import textwrap
        draw.multiline_text(
            (x, 1580), "\n".join(textwrap.wrap(disclaimer, width=34)),
            font=small, fill=muted, spacing=12
        )
    elif graphic == "outro":
        draw.text((x, 360), "SCIENCE", font=large, fill=white)
        draw.text((x, 500), "WITHOUT", font=large, fill=white)
        draw.text((x, 640), "HYPE", font=large, fill=accent)
        draw.line((x, 850, 990, 850), fill=accent, width=6)
        draw.text((x, 980), "Follow for evidence-led", font=medium, fill=white)
        draw.text((x, 1060), "science and news explainers", font=medium, fill=white)
        draw.text((x, 1590), "SOURCES IN DESCRIPTION", font=small, fill=muted)
    else:
        raise ValueError(f"Unsupported story graphic: {graphic}")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, quality=94)
    metadata = {
        "provider": "LabLens/AI Studio local graphic renderer",
        "asset_type": "programmatic_graphic",
        "ai_generated": False,
        "licence_status": "Created by this project; verify project ownership and any embedded font licence before publication.",
        "graphic_kind": graphic,
        "human_visual_inspection_required": True,
    }
    path.with_suffix(path.suffix + ".source.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metadata


def prepare_candidate_script(script: dict, packet: dict, scene_assets: list[dict]) -> dict:
    # Carry the user's approved editorial corrections into the production candidate.
    # The original generated artifact remains unchanged for auditability.
    reviewed_script = json.loads(json.dumps(script))
    if is_himalayan_story(packet, reviewed_script) and len(reviewed_script.get("scenes", [])) >= 4:
        reviewed_script["scenes"][2]["say"] = (
            "2016 से 2023 के बीच, नदी के पास 0–50 मीटर HAND क्षेत्र में "
            "इमारतों का कुल ज़मीनी footprint 46% बढ़ा। मॉडल किए गए GLOF "
            "क्षेत्रों के भीतर यह वृद्धि 77% रही।"
        )
        reviewed_script["scenes"][3]["say"] = (
            "यह मॉडल से निकला संभावित exposure है, बाढ़ की भविष्यवाणी नहीं। "
            "अध्ययन निर्माण और जोखिम के बीच कारण-परिणाम साबित नहीं करता—"
            "यह भी नहीं बताता कि निर्माण से बाढ़ की संभावना बढ़ी या नहीं।"
        )
    candidate = dict(reviewed_script)
    candidate["sources"] = packet["sources"]
    candidate["language"] = "hindi"
    candidate["voice_gender"] = "female"
    candidate["editorial_status"] = "draft_assets_require_human_visual_and_rights_review"
    candidate["visual_review_status"] = "pending_human_visual_and_rights_review"
    candidate["scenes"] = [
        {**scene, **asset}
        for scene, asset in zip(reviewed_script["scenes"], scene_assets)
    ]
    return candidate


def prepare_assets(artifact_dir: str | Path, output_dir: str | Path) -> dict:
    artifact_dir, output_dir = Path(artifact_dir), Path(output_dir)
    packet = json.loads(find_one(artifact_dir, "reviewed_story.json").read_text(encoding="utf-8"))
    script = json.loads(find_one(artifact_dir, "shorts_script_draft.json").read_text(encoding="utf-8"))
    validate_draft(packet, script)

    from core.images import fetch_clip, generate_image
    from core.asset_licensing import build_licence_register

    output_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    used_ids: set = set()
    scene_assets = []
    timeline = []
    himalayan = is_himalayan_story(packet, script)

    for index, scene in enumerate(script["scenes"], start=1):
        plan = visual_plan(index, len(script["scenes"]), himalayan)
        clip_path = assets_dir / f"scene_{index:02d}.mp4"
        image_path = assets_dir / f"scene_{index:02d}.jpg"
        metadata = {}
        if plan.get("kind") == "graphic":
            metadata = create_story_graphic(image_path, plan["graphic"])
            asset_type, asset_path, ai_disclosure = "image", image_path, False
        else:
            if plan.get("kind") == "stock":
                keywords = plan["queries"]
                prompt = (
                    "Photorealistic documentary B-roll of the exact Himalayan mountain river or "
                    "glacial-lake landscape described by the search query. Natural geography, "
                    "no maps, no captions, no logos, no staged laboratory footage."
                )
            else:
                prompt = (
                    "Illustrative, non-archival news documentary visual for this exact narration. "
                    "Do not depict a real identifiable person, imitate a news broadcast, or add text, "
                    "labels, logos, maps with invented boundaries, or unsupported details. "
                    f"Narration: {scene['say']} Visual direction: {scene['image']}. Vertical 9:16."
                )
                keywords = scene.get("keywords") or [scene.get("image", "")]
            if fetch_clip(keywords, clip_path, used_ids, scene_prompt=prompt):
                asset_type, asset_path, ai_disclosure = "video", clip_path, False
                metadata = json.loads(clip_path.with_suffix(clip_path.suffix + ".source.json").read_text(encoding="utf-8"))
                if plan.get("kind") == "stock" and float(metadata.get("relevance_score", 0)) < 0.45:
                    clip_path.unlink(missing_ok=True)
                    clip_path.with_suffix(clip_path.suffix + ".source.json").unlink(missing_ok=True)
                    raise RuntimeError(
                        f"Scene {index} stock candidate failed the stricter relevance threshold; "
                        "refusing to package unrelated footage."
                    )
            elif generate_image(prompt, image_path, category="geopolitics" if himalayan else "general"):
                asset_type, asset_path, ai_disclosure = "image", image_path, True
                metadata = json.loads(image_path.with_suffix(image_path.suffix + ".source.json").read_text(encoding="utf-8"))
            else:
                raise RuntimeError(
                    f"No relevant visual candidate could be prepared for scene {index}; "
                    "the pipeline refuses to insert unrelated filler footage."
                )

        scene_assets.append({
            "asset_type": asset_type,
            "asset_path": str(asset_path),
            "ai_disclosure": ai_disclosure,
            "asset_review_status": "pending_human_visual_and_rights_review",
        })
        timeline.append({
            "_start": float((index - 1) * 5),
            "_media": {"path": str(asset_path), "type": asset_type},
            "visual_prompt": scene.get("image", ""),
            "narration": scene.get("say", ""),
            "on_screen_text": scene.get("caption", ""),
            "source_refs": scene.get("source_ids", []),
        })
        print(f"Prepared scene {index}: {asset_type} via {metadata.get('provider', 'unknown provider')}")

    licence = build_licence_register(timeline, output_dir)
    candidate = prepare_candidate_script(script, packet, scene_assets)
    (output_dir / "production_candidate.json").write_text(
        json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "reviewed_evidence_packet.json").write_text(
        json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        "title": script.get("title"),
        "scene_count": len(scene_assets),
        "assets": [
            {
                "scene": index,
                "type": item["asset_type"],
                "path": item["asset_path"],
                "ai_disclosure_required": item["ai_disclosure"],
                "review_status": item["asset_review_status"],
            }
            for index, item in enumerate(scene_assets, start=1)
        ],
        "licence_register": licence,
        "editorial_status": candidate["editorial_status"],
        "visual_review_status": candidate["visual_review_status"],
        "final_mp4_rendered": False,
        "youtube_upload_performed": False,
        "publication_performed": False,
        "next_gate": (
            "Human must inspect every asset, verify the exact source/licence terms and attribution, "
            "confirm AI disclosure where applicable, then separately approve final rendering."
        ),
    }
    (output_dir / "asset_review_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare scene-relevant news Short visuals for human review; no final render.")
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--output-dir", default="output/daily_news/asset_review")
    args = parser.parse_args()
    prepare_assets(args.artifact_dir, args.output_dir)


if __name__ == "__main__":
    main()
