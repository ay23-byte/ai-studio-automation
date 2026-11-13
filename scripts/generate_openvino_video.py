"""Generate one local OpenVINO LTX clip from the command line."""
import sys as _sys
from pathlib import Path as _Path

if __package__ in (None, ""):  # run as "python scripts/<name>.py": make the repo importable
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
import sys
from pathlib import Path

from core.openvino_video import generate_ltx_video


def main():
    parser = argparse.ArgumentParser(description="Free local LTX-Video generation using OpenVINO GenAI")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--aspect-ratio", choices=["9:16", "16:9"], default="9:16")
    args = parser.parse_args()
    output = Path(args.output)
    if not generate_ltx_video(args.prompt, output, aspect_ratio=args.aspect_ratio):
        print("Generation did not complete. Check OpenVINO model path, GPU driver and logs.")
        return 1
    print(f"Generated review clip: {output}")
    print("This is synthetic AI footage; human review is required before use.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
