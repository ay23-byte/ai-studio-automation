"""Deterministic FFmpeg motion profiles driven by the free Cinema Studio shot plan."""
from __future__ import annotations

MOVEMENTS = {"locked", "slow_push_in", "slow_pull_out", "tracking", "pan", "tilt", "crane", "handheld", "aerial"}

def movement_for(settings: dict | None, index: int = 0) -> str:
    value = str((settings or {}).get("movement") or "").strip().lower()
    return value if value in MOVEMENTS else ("slow_push_in" if index % 2 == 0 else "tracking")

def still_motion_parameters(settings: dict | None, index: int, frames: int):
    """Return zoom, x and y expressions for FFmpeg zoompan."""
    movement = movement_for(settings, index)
    progress = f"min(on/{max(int(frames), 1)},1)"
    center_x, center_y = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    if movement == "locked": return "1.0", center_x, center_y
    if movement == "slow_pull_out": return f"1.055-0.055*{progress}", center_x, center_y
    if movement in {"pan", "tracking", "aerial"}:
        x = f"(iw-iw/zoom)*{progress}" if index % 2 == 0 else f"(iw-iw/zoom)*(1-{progress})"
        return "1.055", x, center_y
    if movement == "tilt":
        y = f"(ih-ih/zoom)*{progress}" if index % 2 == 0 else f"(ih-ih/zoom)*(1-{progress})"
        return "1.055", center_x, y
    if movement == "crane": return f"1.0+0.035*{progress}", center_x, f"(ih-ih/zoom)*(0.25+0.5*{progress})"
    if movement == "handheld": return "1.045", f"{center_x}+2*sin(on/13)", f"{center_y}+2*cos(on/17)"
    return f"1.0+0.055*{progress}", center_x, center_y

def video_crop_expressions(settings: dict | None, index: int, duration: float, max_x: int, max_y: int):
    """Return bounded crop x/y expressions for subtle movement in video B-roll."""
    movement = movement_for(settings, index)
    progress = f"min(t/{max(float(duration), 0.1):.3f},1)"
    cx, cy = f"{max_x}/2", f"{max_y}/2"
    if movement == "locked": return cx, cy
    if movement in {"pan", "tracking", "aerial"}:
        return (f"{max_x}*{progress}" if index % 2 == 0 else f"{max_x}*(1-{progress})"), cy
    if movement == "tilt":
        return cx, (f"{max_y}*{progress}" if index % 2 == 0 else f"{max_y}*(1-{progress})")
    if movement == "crane": return cx, f"{max_y}*(0.25+0.5*{progress})"
    if movement == "handheld": return f"{cx}+2*sin(t*1.7)", f"{cy}+2*cos(t*1.3)"
    direction = "1" if movement == "slow_push_in" else "-1"
    return f"{cx}+({max_x}*0.12*{progress}*{direction})", f"{cy}-({max_y}*0.08*{progress}*{direction})"