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

## Free local AI video generation

An optional local text-to-video backend is now available through
[OpenVINO GenAI](https://openvinotoolkit.github.io/openvino.genai/) and
[LTX-Video](https://huggingface.co/Lightricks/LTX-Video). It does not call
MuAPI, Replicate, Cloudflare Workers AI, or any paid hosted inference API.

The OpenVINO GenAI project documents LTX-Video text-to-video and image-to-video
pipelines, including an INT8 export option that produces a smaller model. It
also documents GPU execution; OpenVINO lists Intel Iris Xe Graphics among
supported GPU hardware. Actual speed and memory use depend on the exact CPU,
driver, RAM, model export and output settings.

### Run generation from GitHub Actions using the Intel Iris Xe GPU

The repository now includes a manual workflow:
`.github/workflows/openvino-ltx-local-gpu.yml`.

To use it without opening VS Code or editing files locally:

1. In the repository, open **Settings → Actions → Runners → New self-hosted runner**.
2. Select **Windows x64** and follow GitHub's displayed runner registration steps on the Windows PC that has Intel Iris Xe.
3. Add the custom runner label `intel-iris-xe` when configuring the runner.
4. Keep the runner service online, then open **Actions → Free local AI video generation — Intel Iris Xe → Run workflow**.
5. Enter a shot prompt, choose 9:16 or 16:9, and run. The workflow installs the free runtime, exports/caches the INT8 LTX model on that runner, generates an MP4 and uploads a review artifact.

The workflow never uploads to YouTube. It does not require a paid API or payment
method. The runner PC must remain powered on and connected while the job runs.
The model export is a large first-time download; later runs reuse the runner's
tool-cache folder.

The generator uses a short, low-resolution LTX configuration by default and
then lets the existing FFmpeg editor upscale, crop and composite the clip.
Tune `OPENVINO_VIDEO_FRAMES`, `OPENVINO_VIDEO_STEPS`, `OPENVINO_VIDEO_WIDTH`
and `OPENVINO_VIDEO_HEIGHT` only after a successful first generation.
If the Intel GPU is not detected, generation fails closed. CPU fallback is
opt-in with `OPENVINO_ALLOW_CPU_FALLBACK=true`; CPU video generation may be
extremely slow.

**Important execution boundary:** GitHub-hosted runners do not contain the
user's Intel Iris Xe GPU. This workflow therefore targets a self-hosted
Windows runner on the user's own Intel PC. The repository's normal GitHub CI
validates the integration without downloading model weights or claiming a
GPU render has been tested.

Generated clips are explicitly marked as AI-generated and require human visual
inspection. They must never be presented as genuine archival footage. Review
the upstream model license and terms before commercial publication. This is
not 3D presenter animation or lip-sync.

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