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

## Use clips in the long-form pipeline

Create one clip from the exact narration audio for each chapter. Convert the downloaded WebM files to MP4 with FFmpeg, then place them in the repository at `assets/presenter_clips/` using the names `presenter_section_01.mp4`, `presenter_section_02.mp4`, and so on. Commit those clips to the repository before running **Actions → make-longform** and select **talking_avatar**. Choose the same male/female voice used to prepare the clips. The long-form renderer inserts each presenter clip at the start of its matching chapter, mutes clip audio, and preserves the single continuous narration track. Clips longer than the chapter narration are rejected.

## Important limitations

- This is a 3D avatar studio, not a photo-to-video system.
- The SDK does not provide direct MP4 export. Browser recording creates WebM; FFmpeg converts it.
- The SDK animates face/mouth, blinks and brows; it does not animate a full body. The studio feeds the narration audio stream into the SDK `attachStream()` API and records that same source audio alongside the avatar canvas.
- The lip-sync model is English-trained. Hindi/Hinglish can work, but mouth shapes may be approximate.
- Automatic audio capture uses the browser media element `captureStream()` API; use a current Chromium-based browser such as Chrome or Edge. Keep the studio tab visible while recording.
- This companion studio creates a presenter clip from an audio file; it does not yet automatically replace every presenter shot inside the main long-form pipeline. The existing `still_cpu` and `sadtalker` modes remain unchanged and safe.

## License and privacy

The SDK and included avatars are MIT-licensed by their respective authors. Audio is processed in the browser; the avatar and lip-sync model are fetched from jsDelivr. No avatar API key or account is used. Review the upstream license and attribution before redistributing.
