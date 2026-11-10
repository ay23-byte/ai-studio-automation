# Free Cinema Studio workflow

This milestone adapts the camera-control and cinematic-prompt workflow from
[Open-Higgsfield-AI](https://github.com/Autom8AI/Open-Higgsfield-AI) without
adopting its hosted MuAPI generation client.

## What is included

- Camera body, lens, focal length, aperture, shot size, movement, lighting and
  aspect-ratio controls represented as deterministic prompt settings.
- Shot-by-shot prompt planning that preserves the original storyboard records.
- JSON export with explicit `prompt_only`, `generation_performed: false`,
  `paid_provider_calls_performed: false`, and human-review flags.
- Regression tests for prompt construction, invalid settings, input immutability
  and export metadata.

## What is deliberately not included

The upstream studio's Image, Video and Lip Sync interfaces submit generation
jobs through hosted model APIs. Listing a model in that UI does not make its
inference free or locally runnable. This integration therefore does not copy
those API calls, model catalogues, API-key forms, upload endpoints, or hosted
generation queue.

No local image/video/lip-sync model is enabled by this milestone. A model may
only be added in a later change after checking hardware requirements, model
license, weights availability, dependencies, and a test proving the backend
does not make network or paid-provider calls.

## Use

```python
from core.cinematic_shot_planner import write_cinematic_shot_plan

report = write_cinematic_shot_plan(
    shot_timeline,
    "build/review/cinematic_shot_plan.json",
    defaults={
        "camera": "70mm_film",
        "lens": "anamorphic",
        "focal_length": 50,
        "aperture": "f/2.8",
        "movement": "slow_push_in",
        "lighting": "natural",
    },
)
```

The JSON is a production prompt sheet, not a rendered video. It does not
authenticate historical footage, verify visual relevance, clear copyright, or
approve publication. Human review remains mandatory.

## Free-production boundary

This code has no inference dependencies and makes no external requests. GitHub
Actions can run its tests and produce the JSON plan without GPU access. Actual
local model inference requires a compatible machine/GPU and separately
validated open weights; GitHub-hosted CPU runners are not treated as a free GPU.

## Rendering integration

The integrated pipeline now writes `cinematic_shot_plan.json` for both Shorts
and long-form builds. The planned camera movement is passed into the existing
FFmpeg editors: still images receive controlled push/pull, pan, tilt or crane
motion; video B-roll receives a bounded crop drift; existing transitions,
narration, subtitles, music and licensing/review gates remain in place.

This is deterministic 2D camera movement over real stills/video, not AI video
generation, 3D scene creation, face animation or lip-sync. It runs on CPU in
GitHub Actions and does not require Intel/NVIDIA GPU inference or paid APIs.