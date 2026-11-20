# Opt-in reviewed visual assets

V2 defaults to labelled synthetic stills. The three existing production workflows now enable the shared Manim/Blender bridge for eligible diagram scenes.
To replace specific v2 scenes, create a JSON file and set `PV2_VISUAL_MANIFEST` to its path:

```json
{
  "scene_01": {"type": "commons", "title": "File:Example.webm", "reviewed": true},
  "scene_02": {"type": "manim_wave", "reviewed": true},
  "scene_03": {"type": "blender_orbit", "reviewed": true}
}
```

Scene IDs must match the generated script; `File:Example.webm` is an example, not an
assertion that that file exists. Select an actual Commons file relevant to the scene and
review its source page and license, attribution, depicted rights and suitability first.
`reviewed: true` is an operator attestation, not an automated legal or factual check.
The Commons adapter retrieves the explicit file and metadata, accepts only exact CC BY,
CC BY-SA (3.0 or 4.0), or CC0 license URLs, requires a creator, records source, attribution
and SHA-256, restricts hosts and size, and fails closed. It does not search for footage,
check relevance, or guarantee that Commons metadata is accurate. Source credits are
written to `licenses.csv` and `credits.txt`. Supported media: JPEG, PNG, WebM, MP4, Ogg
video. Visual clips loop when shorter than the scene and use narration rather than source
audio. Review final output and any loop/repeated footage.

`manim_wave` renders one **conceptual sine wave**, not an automatically accurate
illustration of an arbitrary physics claim. Select it only when relevant. Manim Community
and its system dependencies must be installed separately in the same Python environment;
there is no paid API. Missing Manim is a hard failure, not a silent fallback. This option
is available only for diagram/animation scenes.

`blender_orbit` renders a labelled idealized circular orbit, not to scale. Select it
only for an appropriate diagram/animation scene. Install Blender 4.2 or newer with its
background renderer and FFmpeg; `PV2_BLENDER_BINARY` optionally specifies the Blender
executable (default `blender`). Alternatively, set `PV2_BLENDER_BACKEND=bpy` when the
official `bpy` package is installed in the same Python environment (CI uses version 4.2.23). Blender renders to temporary PNG frames which are encoded
to H.264 and removed. An absent tool or incomplete render fails the run. Rendering at
1080p is CPU/memory intensive; measure runtime and budget before any production use.
No arbitrary scripts, blend files, third-party code or scene parameters are accepted.
The two examples are not a general scientific animation library: additional models and
scientific validation are needed before using animations for other concepts.

The Commons adapter has offline mocked tests. Real Manim 0.19 and Blender 4.2.23
render-and-probe tests passed on GitHub Actions in run 44716249833. The production
workflows install the pinned open-source renderers and run technical validation before
editing. Live Wikimedia retrieval and full archival documentary production still need
separate verification. To repeat the small renderer integration tests, run
`PV2_TEST_REAL_ANIMATIONS=1 python -m pytest tests/test_v2_real_animation.py -q`.

## Automatic draft selection (opt-in)

Set `PV2_ANIMATION_MODE=auto_strict` to select a renderer from the generated scene
narration. Exact phrases `sinusoidal wave` / `sine wave` select Manim; `circular orbit`
selects Blender. These narrow phrase matches are not scientific validation. Unsupported,
negated or ambiguous claim scenes are not animated; they never silently receive an
unrelated animation. Intro/outro graphics remain illustrative. Existing explicitly
reviewed manifest choices take priority. Choices are recorded in `visual_selection.json`
and the asset manifest.

Automatically selected animations do not require a human-approval flag. They are
eligible only when narration positively names one supported concept and the storyboard
requests a diagram/animation. Rendered clips are probed for a valid video stream,
resolution and duration; the final video still passes the pipeline's technical, motion,
audio, subtitle, research and rights checks. These are machine checks, not a guarantee
that software can establish scientific truth. The automatic catalogue remains limited to
sine waves and idealized circular orbits. The isolated v2 stack keeps
`PV2_ANIMATION_MODE=off` by default; the three production workflows enable it explicitly.
