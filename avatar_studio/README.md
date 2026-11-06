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
4. Click **Record avatar clip**. In the browser prompt, select this tab and enable **Share tab audio**.
5. Click **Stop & save recording** after the narration finishes. The browser downloads `talking_presenter.webm`.

## Convert to MP4

If FFmpeg is installed:

```powershell
ffmpeg -y -i talking_presenter.webm -c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 192k talking_presenter.mp4
```

## Important limitations

- This is a 3D avatar studio, not a photo-to-video system.
- The SDK does not provide direct MP4 export. Browser recording creates WebM; FFmpeg converts it.
- The SDK animates face/mouth, blinks and brows; it does not animate a full body.
- The lip-sync model is English-trained. Hindi/Hinglish can work, but mouth shapes may be approximate.
- Browser recording requires a user permission step and tab audio capture. Keep the tab visible.
- This companion studio creates a presenter clip from an audio file; it does not yet automatically replace every presenter shot inside the main long-form pipeline. The existing `still_cpu` and `sadtalker` modes remain unchanged and safe.

## License and privacy

The SDK and included avatars are MIT-licensed by their respective authors. Audio is processed in the browser; the avatar and lip-sync model are fetched from jsDelivr. No avatar API key or account is used. Review the upstream license and attribution before redistributing.
