# Talking Avatar Studio

A browser-based companion tool for AI Studio. It uses the MIT-licensed `@interviewflowai/talking-avatar` SDK and its built-in male/female 3D avatars to animate a narration audio file.

## Run locally (Windows)

From the repository root:

```powershell
python -m http.server 8765
```

Open [http://localhost:8765/avatar_studio/](http://localhost:8765/avatar_studio/) in Chrome or Edge.

1. Choose male or female presenter.
2. Select an MP3/WAV narration file.
3. Click **Load presenter** and wait for the model to load.
4. Click **Record avatar clip**. The Studio captures the selected narration audio directly; no screen-sharing prompt is required.
5. Wait for the narration to finish. The browser automatically saves `talking_presenter.webm`.

## Convert to MP4

If FFmpeg is installed:

```powershell
ffmpeg -y -i talking_presenter.webm -c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 192k talking_presenter.mp4
```

## Automatic 3D avatar generation in GitHub Actions

The documentary workflow offers `talking_avatar_auto` and selects it by default. It generates the narration first, cuts the opening seconds of each chapter out of it, and renders one presenter clip per chapter with `render-offline.mjs`.

**How the clips are rendered.** Nothing is recorded in real time. `offline.html` replaces the page clock with a virtual clock; the driver advances it by exactly one frame, lets the avatar draw that frame, saves the picture and moves on. Frame *k* always shows the mouth for narration time *k / fps*, however long the frame took to draw. FFmpeg encodes the frames at a constant 30 fps and adds the chapter's own narration as the sound track. A slow runner makes the render take longer; it cannot lower the frame rate or shift the lip movement. (The earlier real-time recorder captured 0.57 frames per second on a CPU runner.)

**What the presenter looks like.** Head-and-shoulders framing, arms at rest, a studio backdrop drawn in code, blinking, and small head and neck movement that follows the loudness of the narration.

**What is checked.** After rendering, `core/presenter_quality.py` decodes every clip and measures: how many frames are really different (at least 15 per second, repeated pictures do not count), the longest freeze (0.5 s at most), whether the mouth region moves in the frames where narration is audible, and whether the picture is in step with the mouth movement the renderer recorded. A clip that fails is rejected with the reason; there is no fallback to a still portrait. These measurements do not prove convincing lip-sync: every clip is marked `human_visual_review: pending` until a person has watched it.

**Setup** (done by the workflow):

```bash
cd avatar_studio
npm ci                                   # three, the avatar SDK and Playwright, pinned in package-lock.json
npx playwright install --with-deps chromium
```

The 3D engine, the SDK, both avatars and the lip-sync model come from `node_modules`; no CDN is contacted while rendering. Settings: `AVATAR_GENDER` (`female`/`male`), `AVATAR_FPS` (24, 25 or 30; default 30), `AVATAR_WIDTH`/`AVATAR_HEIGHT` (default 1920x1080), `AVATAR_FRAMING` (`medium`/`close`).

To check a folder of clips by hand: `python scripts/check_presenter_clips.py assets/presenter_clips --report presenter_report.json`. It also writes a face contact sheet beside each clip.

Lip-sync for Hindi and Hinglish narration is approximate (the model was trained on English speech sounds) and needs a person's judgement.

## Use clips in the long-form pipeline

Create one clip from the exact narration audio for each chapter. Convert the downloaded WebM files to MP4 with FFmpeg, then place them in the repository at `assets/presenter_clips/` using the names `presenter_section_01.mp4`, `presenter_section_02.mp4`, and so on. Commit those clips to the repository before running **Actions → make-longform** and select **talking_avatar**. Choose the same male/female voice used to prepare the clips. The long-form renderer inserts each presenter clip at the start of its matching chapter, mutes clip audio, and preserves the single continuous narration track. Clips longer than the chapter narration are rejected.

## Important limitations

- This is a 3D avatar studio, not a photo-to-video system.
- The SDK does not provide direct MP4 export. The live studio page (`index.html`) records WebM for FFmpeg to convert; the automatic renderer writes MP4 itself.
- The SDK animates face/mouth, blinks and brows; it does not animate a full body (the offline renderer adds head and neck movement and a resting arm pose). The live studio feeds the narration audio stream into the SDK `attachStream()` API and records that same source audio alongside the avatar canvas.
- The lip-sync model is English-trained. Hindi/Hinglish can work, but mouth shapes may be approximate.
- Automatic audio capture uses the browser media element `captureStream()` API; use a current Chromium-based browser such as Chrome or Edge. Keep the studio tab visible while recording.
- The presenter is a stylised 3D character, not a photorealistic person. It opens each chapter for a few seconds; footage and graphics carry the rest. The existing `still_cpu` and `sadtalker` modes remain unchanged.

## License and privacy

The SDK and included avatars are MIT-licensed by their respective authors. Audio is processed in the browser. The live studio page fetches the avatar and lip-sync model from jsDelivr; the automatic renderer uses the pinned npm packages and contacts no CDN. No avatar API key or account is used. Review the upstream license and attribution before redistributing.
