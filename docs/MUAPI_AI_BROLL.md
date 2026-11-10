# Optional MuAPI AI B-roll provider

The long-form renderer can request generated video clips from MuAPI as an **optional** B-roll source. It does not import or run the Open-Higgsfield-AI React studio. It uses the same MuAPI submit/poll API pattern described by the Open-Higgsfield-AI project and MuAPI's API reference.

## Safety defaults

- Disabled by default. The GitHub Actions input `muapi_video_enabled` must be checked for a run.
- Requires the repository secret `MUAPI_API_KEY`.
- Requires a documented model endpoint in repository variable `MUAPI_VIDEO_ENDPOINT`.
- Requires model-specific JSON in repository variable `MUAPI_VIDEO_INPUT_JSON` when the chosen model needs parameters beyond `prompt`.
- Per-request cost ceiling defaults to USD 0.25. Override with `MUAPI_MAX_COST_USD` only after checking current model pricing.
- A quote/final charge above the ceiling is rejected before the output is downloaded.
- API failure returns to the existing visual-provider fallback path; it does not block archival discovery.
- AI outputs are written with `.source.json` provenance metadata, `ai_generated_video` type, and AI disclosure. They are not represented as real archival footage.
- AI assets remain subject to provider/model terms review, visual/editorial review, and existing release certification. No human approval is created automatically.

## GitHub repository configuration

1. Add **Settings → Secrets and variables → Actions → Secrets**: `MUAPI_API_KEY`.
2. Add **Variables**:
   - `MUAPI_VIDEO_ENDPOINT`: exact endpoint slug for the chosen model from the live MuAPI model documentation.
   - `MUAPI_VIDEO_INPUT_JSON`: exact JSON object required by that model. The adapter overwrites its `prompt` field with the shot prompt.
   - Optional `MUAPI_MAX_COST_USD`: maximum acceptable USD charge per generated clip (default 0.25).
3. Run **Actions → make-longform** and explicitly enable **Enable optional paid MuAPI AI B-roll generation** only when you intend to use paid generation.
4. Keep YouTube upload disabled for the first test. Download and inspect the review package before approving publication.

Example input JSON is model-specific; this is only a shape example, not a recommendation for a particular endpoint:

```json
{"prompt":"","duration":5,"aspect_ratio":"16:9"}
```

Do not copy that example blindly. Check the selected model's live endpoint, required fields, duration/resolution limits, price, and provider/model terms first.

## What is not included

This integration does not automatically install the upstream studio UI, expose all models, guarantee free credits, create real presenter lip-sync, or guarantee that generated clips match the script. Lip-sync and image-to-video are deliberately not enabled by this first adapter. A real provider run is required to verify the chosen endpoint, schema, output format, actual charge, and visual quality.
