# Cinematic news renderer

The reviewed-news render stage now builds an original vertical motion-graphics film
instead of relying on a sequence of still images with a Ken Burns effect.

## Current visual language

- **Visual Investigation:** evidence-file cards, source-reference band, connected
  claim/context/source motifs, subtle technical grid and moving scan line.
- **Broadcast News:** headline card, lower-third context panel, source-reference band,
  progress rail and restrained accent motion.
- Scene selection can be supplied with `visual_style` or `scene_type`; otherwise
  the renderer alternates the two treatments.
- The renderer uses only reviewed script fields such as `caption`, `label`,
  `headline`, `kicker` and `source_ids`. It does not invent statistics, maps,
  photographs, footage, or presenter performance.

## Technical contract

`core.cinematic_renderer.render_cinematic_video(scenes, durations, output_path)`
produces a silent 1080×1920 H.264 video at 30 fps. Its frame function is deterministic
for the same scene and time inputs. The existing review pipeline then muxes the
already-synthesized narration and Hindi ASS subtitles onto that video.

FFmpeg and Pillow are required. GitHub Actions installs FFmpeg and Pillow in the
review-render workflow. The complete unit-test suite runs before any render.

## Review and limitations

This is an original code-generated motion-graphics system, not a text-to-video model.
It does not create real footage, animate a portrait's mouth, or provide presenter
lip-sync. The current renderer is an initial visual system; each produced MP4 still
requires human review for story accuracy, visual relevance, pronunciation, captions,
audio, rights and overall quality. Rendering remains review-only: upload and publish
are false and no YouTube action is performed.
