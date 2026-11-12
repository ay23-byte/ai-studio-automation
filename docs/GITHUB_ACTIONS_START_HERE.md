# GitHub Actions — Start Here

Use these three numbered workflows for normal production:

1. **1. Create a Short — review first**  
   Open [Create a Short](../actions/workflows/make_shorts.yml). Enter a topic and choose the language. Leave the upload option disabled. The run builds a Short and stores its output as an Actions artifact for review. The existing scheduled run is preserved.

2. **2. Create a Documentary — review first**  
   Open [Create a Documentary](../actions/workflows/make_longform.yml). Enter the topic, duration, language, voice and visual settings. Review the generated artifact before any upload.

3. **3. Upload Approved Documentary — Private**  
   Open [Private documentary upload](../actions/workflows/manual-youtube-private-upload.yml) only after watching the complete MP4 and checking its facts, visuals, audio and rights. Supply the source render run ID and confirm the human-review checkbox. This workflow uploads as **Private**, not Public.

## What the ⚙ Internal label means

Workflows prefixed with **⚙ Internal** are specialist steps, diagnostics, provider checks, experimental renderers, or parts of multi-step handoffs. Avoid them for an ordinary Short or documentary. They are retained because some are scheduled, provide validation, or are required for specialist workflows.

The daily news research queue remains scheduled. Specialist workflow triggers and underlying code are preserved. This cleanup changes display names and adds a clear starting point; it does not delete or disable workflows.

## Important limitation

The news one-click workflow currently prepares a script/visual review package; it is not the final MP4 renderer or YouTube uploader. It is therefore marked Internal until the full review-to-MP4 path is consolidated and verified.
