# Opt-in reviewed visual assets

V2 defaults to labelled synthetic stills, and existing production workflows are unchanged.
To replace specific v2 scenes, create a JSON file and set `PV2_VISUAL_MANIFEST` to its path:

```json
{
  "scene_01": {"type": "commons", "title": "File:Example.webm", "reviewed": true},
  "scene_02": {"type": "manim_wave", "reviewed": true}
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
is available only for diagram/animation scenes. Blender integration and additional
scientific simulations are **not implemented**.

The Commons adapter and Manim invocation have offline mocked tests; a real FFmpeg video
segment was rendered locally. Live Wikimedia retrieval, a real Manim render, and a full
archival documentary have **not been verified** in this sandbox. A human must review
assets and the complete film, and private upload still requires separate live verification.
