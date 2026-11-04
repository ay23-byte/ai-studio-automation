"""Generate a YouTube thumbnail (1280x720) from the first AI image + title text."""
import subprocess
from pathlib import Path

def make_thumbnail(image_path: Path, title: str, out: Path, channel: str = "Power Codex"):
    """Overlay bold title text on the AI image, sized for YouTube thumbnails."""
    title_clean = title[:60].replace("'", "\\'").replace(":", " -")
    channel_clean = channel.replace("'", "\\'")
    vf = (
        f"scale=1280:720:force_original_aspect_ratio=increase:flags=lanczos,crop=1280:720,"
        f"eq=brightness=-0.12:saturation=1.1,"
        f"drawtext=fontfile=/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf:"
        f"text='{title_clean}':"
        f"fontcolor=white:fontsize=56:borderw=4:bordercolor=black:"
        f"x=(w-text_w)/2:y=h*0.62:line_spacing=12,"
        f"drawtext=fontfile=/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf:"
        f"text='{channel_clean}':"
        f"fontcolor=0xFFD700:fontsize=34:borderw=3:bordercolor=black:"
        f"x=(w-text_w)/2:y=h*0.85"
    )
    if image_path.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                        "-loop", "1", "-i", str(image_path),
                        "-vf", vf, "-frames:v", "1", str(out)], check=True)
    else:
        # no image: dark gradient background
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                        "-f", "lavfi", "-i", "color=c=0x0a1a3a:s=1280x720",
                        "-vf", vf, "-frames:v", "1", str(out)], check=True)
    return out
