# Opt-in reviewed visual assets

V2 defaults to labelled synthetic stills, and existing production workflows are unchanged.
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
The two examples are not a general scientific animation library: additional models,
scientific validation and artist review remain necessary.

The Commons adapter has offline mocked tests. Real Manim 0.19 and Blender 4.2.23
render-and-probe tests passed on GitHub Actions in run 44716249833. Live Wikimedia
retrieval, final-video human inspection and a full archival documentary remain unverified.
Once Blender, Manim, and FFmpeg are installed, run
`PV2_TEST_REAL_ANIMATIONS=1 python -m pytest tests/test_v2_real_animation.py -q` to run
real, small renderer integration tests (the opt-in test fails if tools are missing). A human must review
assets and the complete film, and private upload still requires separate live verification.

## Automatic draft selection (opt-in)

Set `PV2_ANIMATION_MODE=auto_strict` to select a renderer from the generated scene
narration in any v2 pipeline. Exact phrases `sinusoidal wave` / `sine wave` select
Manim; `circular orbit` selects Blender. These keyword matches are not scientific
validation. Unsupported, negated or ambiguous claim scenes fail before narration; they
never silently receive an unrelated animation. Intro/outro graphics remain illustrative.
Existing explicitly reviewed manifest choices take priority. Choices are recorded in
`visual_selection.json` and the asset manifest.

**Automatically selected animations block visual QA and upload eligibility** until a
human reviews the science and final visual. An operator can supply a reviewed manifest
for the scenes and rerun, but a manifest is an attestation, not proof of review of the
exact rendered output. A complete film still needs human inspection before release.
The automatic catalogue is limited to these two concepts; it does not yet cover arbitrary
physics, research claims or scientific simulations. Defaults and production workflows are
unchanged; `PV2_ANIMATION_MODE=off` remains the default.
