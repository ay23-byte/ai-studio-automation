# Intel OpenVINO presenter (Windows / Iris Xe)

## What is included

The project now has an optional `openvino` presenter mode. It:

- checks that OpenVINO can see the requested device (GPU by default);
- extracts the exact narration segment for each chapter;
- invokes a separately installed audio-driven presenter runner;
- verifies that the runner produced an MP4 with the expected duration; and
- falls back to the existing CPU still presenter if the optional renderer fails.

OpenVINO runtime installation and GPU detection are **not** themselves a lip-sync model. This repository intentionally does not download or redistribute pretrained face-animation weights. You must supply a compatible renderer and confirm its model licence and terms before using it, especially for monetized videos.

## Windows setup

Create/use the isolated environment:

```powershell
conda activate presenter
python -m pip install -r requirements-intel.txt
python -c "from openvino import Core; print(Core().available_devices)"
```

Expected on a correctly configured Iris Xe system: `CPU` and `GPU`. If `GPU` is absent, update the Intel graphics driver and verify OpenVINO's Windows GPU plugin prerequisites.

## Renderer contract

Set these environment variables in the local `.env` file (do not commit secrets):

- `PRESENTER_MODE=openvino`
- `PRESENTER_ENABLED=1`
- `OPENVINO_DEVICE=GPU`
- `OPENVINO_PYTHON=C:\\Users\\YOUR_USER\\anaconda3\\envs\\presenter\\python.exe`
- `OPENVINO_PRESENTER_SCRIPT=C:\\path\\to\\your\\licensed_renderer.py`
- `PRESENTER_IMAGE=assets/presenter/presenter_master.png` (or the female portrait)

The configured renderer is called once per chapter with:

```text
python renderer.py --audio narration.wav --image portrait.png --output presenter.mp4 --device GPU
```

It must create the requested MP4 and return a non-zero exit code on failure. Audio is extracted as mono 16 kHz WAV. The pipeline currently limits presenter inserts to 2–8 seconds per chapter to control render time.

## Fallback and privacy

If OpenVINO, the GPU, the renderer, the portrait, or the output is unavailable, the long-form pipeline logs a warning and uses the existing CPU still presenter. Set `PRESENTER_MODE=still_cpu` for no model dependencies, or `off` to disable the presenter.

YouTube upload remains controlled by the existing workflow input and privacy configuration. Review the finished video before publishing.

## Limitations

- No lip-sync model or weights are bundled in this repository.
- No claim is made that arbitrary ONNX/PyTorch Wav2Lip checkpoints can run directly through OpenVINO. Model conversion, preprocessing, face detection, audio features, and postprocessing are model-specific.
- The external renderer must be tested on the target Windows machine and licensed for the intended use.
