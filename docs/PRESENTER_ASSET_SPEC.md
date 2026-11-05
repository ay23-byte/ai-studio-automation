# Documentary Presenter Asset Specification

## Approved presenter identity

- **Identity:** Original fictional adult Indian male documentary presenter.
- **Presentation:** Credible, composed, intelligent, calm and authoritative; natural expression rather than exaggerated influencer energy.
- **Wardrobe:** Formal charcoal or deep-navy blazer, clean light neutral shirt, no tie by default. No visible brand marks, badges or distracting accessories.
- **Studio:** Professional dark documentary studio; charcoal/black background with subtle warm practical light and restrained cool-blue accent lighting. Background should remain uncluttered.
- **Framing:** Mid-chest/waist-up, shoulders fully visible, looking directly into camera. Keep the face near the upper third and leave safe space around the head and shoulders.
- **Visual style:** Photorealistic, cinematic but natural skin texture, realistic Indian features, soft key light, gentle rim light, sharp eyes, neutral color grade. Avoid plastic skin, over-sharpening, glamour retouching and caricature.
- **Continuity:** Use one approved master portrait as the reference for every future presenter asset. Do not change face shape, hair, age presentation, wardrobe palette or lighting between episodes without an explicit brand decision.

## Required master asset

| Property | Specification |
|---|---|
| File | `assets/presenter/presenter_master.png` |
| Orientation | Portrait, preferably 4:5 or 3:4 |
| Resolution | At least 1536 px on the shortest side; 2048 px preferred |
| Format | PNG; high-quality JPEG acceptable if file size matters |
| Composition | Mid-chest-up, centered, frontal, neutral closed-mouth expression |
| Background | Dark professional studio, low visual detail |
| Quality | Clean face/hair edges, no text, watermark, logo or compression artifacts |
| Rights | Newly generated/original fictional character or a portrait with documented commercial-use permission |

## Optional supporting assets

- `presenter_cutout.png`: same approved presenter with transparent background, if a clean cutout can be produced.
- `presenter_thumbnail.png`: same identity, slightly more expressive but still credible, for thumbnail compositions.
- `presenter_profile.json`: asset version, generation/source notes, license/permission notes, and approval date. Never store private personal data in this file.

## Image-generation brief

Create a photorealistic, original fictional adult Indian male documentary presenter, mid-chest-up, facing camera with a calm and confident neutral expression. He wears a well-fitted charcoal or deep-navy formal blazer over a simple light neutral shirt. Place him in a professional dark documentary studio with a subtle charcoal background, soft cinematic key light on the face and a restrained rim light. Natural Indian facial features, realistic skin texture, sharp eyes, natural proportions, clean professional grooming, premium factual-documentary visual language. Keep the framing symmetrical with shoulders visible and generous safe margins. No microphone blocking the face, no text, no logos, no watermark, no other people, no exaggerated smile, no artificial plastic skin. The result must look like a consistent real studio portrait, not an illustration.

## CPU-first implementation plan

The channel has no GPU available. Therefore the default presenter path must not depend on SadTalker, Wav2Lip, CUDA, a hosted GPU runner or a paid avatar API.

1. **Asset intake and validation**
   - Add the approved master portrait at `assets/presenter/presenter_master.png`.
   - Validate that the file exists, is readable, has sufficient dimensions and has a supported image format.
   - Fail with a clear actionable message if the asset is missing; do not silently substitute a random stock image.

2. **CPU presenter scene renderer**
   - Add a dedicated module such as `core/presenter_scene.py`.
   - Use FFmpeg to create a 1920x1080 scene from the still portrait and the exact narration audio segment for that chapter.
   - Use restrained Ken Burns movement (slow zoom/pan), subtle background treatment, and optional dark gradient/side panel for captions.
   - Preserve the full narration audio; do not imply that still-image animation is real lip-sync.
   - Provide configurable placement (left/right/center), crop mode, zoom strength and scene duration.

3. **Pipeline integration**
   - Add a CPU presenter mode, enabled through configuration and selected as the default presenter mode when a portrait is present.
   - Insert short presenter appearances at the opening and selected chapter transitions; do not keep the same talking-head still on screen for the entire 10–15 minute video.
   - Use B-roll, maps, charts, archival/public-domain/licensed visuals and evidence screenshots for the main explanation.
   - Keep all scene durations aligned to the narration timeline and validate the rendered clip duration with ffprobe.

4. **Editorial and accessibility layer**
   - Add a restrained lower-third for the presenter name/role only if the channel supplies an approved display name.
   - Burn or mux Hindi/Hinglish subtitles using the existing subtitle pipeline.
   - Keep captions away from the presenter's face and important visual evidence.
   - Do not fabricate a personal biography, credentials, institution or expert status for the fictional presenter.

5. **Configuration**
   - Add settings such as `PRESENTER_MODE=still_cpu`, `PRESENTER_IMAGE=assets/presenter/presenter_master.png`, and `PRESENTER_ENABLED=1`.
   - Keep SadTalker as an explicitly optional experimental mode requiring a separately provisioned compatible environment; never make it a default or a requirement for standard GitHub Actions.
   - Document that GitHub-hosted runners are not guaranteed to provide GPU acceleration.

6. **Tests and release checks**
   - Unit-test missing asset, invalid image, disabled presenter and valid configuration paths.
   - Test scene duration, output dimensions, audio presence and ffprobe readability.
   - Add a short sample render to CI only if FFmpeg is available and execution time is reasonable; otherwise test command construction and keep a manual render check.
   - Verify that the final video still renders successfully with presenter mode disabled.
   - Check portrait licensing and all B-roll/music rights before publishing.

## Acceptance criteria

- Runs on a CPU-only machine using FFmpeg; no CUDA or GPU dependency in the default path.
- Uses the same approved presenter identity consistently.
- Presenter scenes use the correct chapter narration segment and do not cut off or duplicate audio.
- Presenter shots are short, deliberate visual anchors; documentary evidence remains the primary visual content.
- Output remains 16:9 at 1920x1080 (or configured project resolution), with valid audio and subtitles.
- README and environment example clearly distinguish CPU still-image animation from genuine AI lip-sync.

## Important limitation

A still portrait with pan/zoom and audio is **not lip-sync**. It is a CPU-friendly presenter scene. Genuine mouth movement requires a separate model/runtime and typically GPU acceleration; it should remain optional until a compatible no-cost CPU or GPU execution environment is actually tested.
