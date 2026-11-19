"""Free narration adapters with an explicit, labelled offline mock backend."""
from __future__ import annotations

import asyncio
import importlib.util
import math
import re
import shutil
import struct
import wave
from pathlib import Path
from typing import Protocol

from .config import V2Config
from .errors import ProviderError
from .models import NarrationSegment, Script


def apply_pronunciation(text: str, mapping: dict[str, str]) -> str:
    for word, replacement in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"\b{re.escape(word)}\b", str(replacement), text, flags=re.I)
    return text


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


class Narrator(Protocol):
    backend: str

    def synthesize(self, script: Script, output_dir: Path, config: V2Config) -> list[NarrationSegment]: ...


class MockNarrator:
    """Deterministic tone narration for offline pipeline tests, never for publication."""

    backend = "mock_offline_tone"

    def synthesize(self, script: Script, output_dir: Path, config: V2Config) -> list[NarrationSegment]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        segments: list[NarrationSegment] = []
        cursor = 0.0
        for index, scene in enumerate(script.scenes, start=1):
            text = apply_pronunciation(scene.narration, config.pronunciation_map)
            words = max(1, len(text.split()))
            duration = min(6.0, max(1.2, words * 0.32))
            path = output_dir / f"narration_{index:02d}.wav"
            _write_tone(path, duration, 220 + (index % 5) * 40)
            segments.append(NarrationSegment(
                id=scene.id, text=text, start=round(cursor, 3),
                end=round(cursor + duration, 3), audio_path=str(path), voice=self.backend))
            cursor += duration
        return segments


def _write_tone(path: Path, duration: float, frequency: float) -> None:
    rate = 16_000
    frames = int(rate * duration)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        for i in range(frames):
            envelope = min(1.0, i / (rate * 0.05), (frames - i) / (rate * 0.05))
            sample = int(9000 * envelope * math.sin(2 * math.pi * frequency * i / rate))
            handle.writeframes(struct.pack("<h", sample))


class EspeakNarrator:
    backend = "espeak-ng-free-fallback"

    def synthesize(self, script: Script, output_dir: Path, config: V2Config) -> list[NarrationSegment]:
        binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if not binary:
            raise ProviderError("neither espeak-ng nor espeak is installed")
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        segments: list[NarrationSegment] = []
        cursor = 0.0
        for index, scene in enumerate(script.scenes, start=1):
            text = apply_pronunciation(scene.narration, config.pronunciation_map)
            path = output_dir / f"narration_{index:02d}.wav"
            import subprocess
            result = subprocess.run([binary, "-w", str(path), "-s", "165", text],
                                    capture_output=True, text=True, timeout=60)
            if result.returncode or not path.is_file():
                raise ProviderError(f"espeak failed for {scene.id}: {result.stderr[:300]}")
            duration = _wav_duration(path)
            segments.append(NarrationSegment(id=scene.id, text=text, start=round(cursor, 3),
                                             end=round(cursor + duration, 3),
                                             audio_path=str(path), voice=self.backend))
            cursor += duration
        return segments


class EdgeFreeNarrator:
    """Unpaid, unofficial Edge TTS adapter; falls back through the selector."""

    backend = "edge-tts-free"

    def synthesize(self, script: Script, output_dir: Path, config: V2Config) -> list[NarrationSegment]:
        if importlib.util.find_spec("edge_tts") is None:
            raise ProviderError("edge_tts is not installed")
        import edge_tts
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        segments: list[NarrationSegment] = []
        cursor = 0.0

        async def run() -> None:
            nonlocal cursor
            for index, scene in enumerate(script.scenes, start=1):
                text = apply_pronunciation(scene.narration, config.pronunciation_map)
                path = output_dir / f"narration_{index:02d}.mp3"
                await edge_tts.Communicate(text, config.tts_voice).save(str(path))
                # MP3 duration is read from the script's planned timing; the editor pads audio.
                duration = scene.visual.duration
                segments.append(NarrationSegment(id=scene.id, text=text, start=round(cursor, 3),
                                                 end=round(cursor + duration, 3),
                                                 audio_path=str(path), voice=self.backend))
                cursor += duration

        try:
            asyncio.run(run())
        except Exception as exc:  # noqa: BLE001 - provider instability must be recoverable
            raise ProviderError(f"Edge TTS failed: {str(exc)[:300]}") from exc
        return segments


def select_narrator(config: V2Config) -> tuple[Narrator, list[str]]:
    """Choose a free backend. Never select a paid service."""
    warnings: list[str] = []
    if config.provider == "mock":
        return MockNarrator(), ["mock narration is for offline tests, not publication"]
    if importlib.util.find_spec("edge_tts") is not None:
        return EdgeFreeNarrator(), ["selected edge-tts (free, unofficial); verify voice quality before release"]
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        return EspeakNarrator(), ["selected espeak-ng (free, accessibility quality); review before release"]
    warnings.append("no free speech backend available; using labelled offline mock")
    return MockNarrator(), warnings
