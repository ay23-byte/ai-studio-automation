"""Create an original scene pack for the existing Short renderer, no stock or remote AI.

The diagram is drawn in the hub's translating reference frame; velocity arrows
are measured relative to the road. This is an illustration, not archival footage.
"""
import math
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.ffmpeg_utils import ffmpeg_bin
from core.fonts import load_font
from core.graphic_scenes import _write_graphic_provenance
from core.jobs import stable_hash
from core.review_contract import sha256_file, write_json

LINES = [
    ('Watch the rim', 'Follow the hub of a wheel rolling to the right on a flat road without slipping.'),
    ('Move the hub', 'The hub moves forward. If you imagine carrying the wheel without turning it, every point moves forward together.'),
    ('Add the spin', 'Now spin it clockwise around the hub. At the bottom, rotation carries the rim backward relative to the hub.'),
    ('At contact', 'At contact, the backward speed exactly cancels the forward speed. For that instant the point is at rest relative to the road.'),
    ('At the top', 'At the top those velocities add instead. The top point moves forward twice as fast as the hub.'),
]


def create(root):
    root.mkdir(parents=True, exist_ok=True)
    script = {'title': 'The point on a rolling wheel that stands still', 'format': 'shorts',
              'category': 'physics', 'hook': 'One wheel. Three speeds.', 'language': 'english',
              'scenes': [{'say': line, 'caption': line, 'image': title + ' on a rolling wheel',
                          'label': title, 'keywords': ['rolling wheel', 'velocity']} for title, line in LINES]}
    rows = []
    font = load_font(18, True, 'ROLLING')
    for index, (title, _) in enumerate(LINES):
        path = root / f'wheel{index}.mp4'
        command = [ffmpeg_bin(), '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '360x640',
                   '-framerate', '24', '-i', '-', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)]
        with subprocess.Popen(command, stdin=subprocess.PIPE) as enc:
            for frame in range(240):
                # Monitor the rotating marker and track road ticks moving beneath the hub.
                a = frame * math.tau / 120
                im = Image.new('RGB', (360, 640), '#132338'); d = ImageDraw.Draw(im)
                for y in range(0, 640, 24): d.line((0, y, 360, y), fill='#19314a')
                for x in range(-32, 400, 32):
                    shift = (a * 88) % 32
                    d.line((x-shift, 420, x-shift-8, 427), fill='#8498a1', width=2)
                d.line((0, 419, 360, 419), fill='#a5bdc4', width=3)
                d.ellipse((92,243,268,419), outline='#83d8d8', width=6)
                for j in range(8):
                    b = a + j*math.tau/8
                    d.line((180,331,180+85*math.cos(b),331+85*math.sin(b)), fill='#559baf', width=3)
                x, y = 180+87*math.cos(a), 331+87*math.sin(a)
                d.ellipse((x-6,y-6,x+6,y+6), fill='#ffbd59')
                for y, dx, text in ([(331, 48, 'v')] if index < 2 else
                        [(407, -48, 'spin')] if index == 2 else [(419, 0, '0')] if index == 3 else [(243, 96, '2v')]):
                    d.line((180,y,180+dx,y), fill='#ffd48f', width=5)
                    if dx:
                        tip = 180+dx; base = tip-math.copysign(10,dx)
                        d.polygon([(tip,y),(base,y-6),(base,y+6)],fill='#ffd48f')
                    d.text((184+dx,y-28),text,font=font,fill='#ffdc96')
                d.text((35,174), f'KINEMATICS   /   0{index+1}',font=font,fill='#84b9cd')
                enc.stdin.write(im.tobytes())
            enc.stdin.close()
            if enc.wait(): raise RuntimeError('Could not encode wheel scene')
        _write_graphic_provenance(path, 'kinematics simulation', title)
        rows.append({'file':path.name, 'sha256':sha256_file(path)})
    write_json(root/'script.json', script)
    write_json(root/'scene_pack.json', {'script_hash':stable_hash(script),'scenes':rows})


if __name__ == '__main__':
    create(Path(sys.argv[1]))
