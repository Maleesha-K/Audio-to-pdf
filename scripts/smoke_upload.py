"""Exercise M4A decoding, local transcription, and lesson creation."""
from __future__ import annotations

import tempfile
import wave
import sys
from pathlib import Path

from faster_whisper.audio import decode_audio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.analysis import process  # noqa: E402


def main():
    audio = decode_audio(str(ROOT / "samples" / "optimization.m4a"))
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temporary:
        folder = Path(temporary)
        wav = folder / "audio.wav"
        with wave.open(str(wav), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes((audio[:16*16000] * 32767).astype("<i2").tobytes())
        lesson = process(ROOT / "samples" / "optimization.pdf", wav, folder,
                         ROOT / "data" / "models")
        assert lesson["points"] and lesson["duration"] > 0
        assert all(p["action"] in {"hold", "underline", "box"} for p in lesson["points"])
        print(f"Created lesson: {len(lesson['pages'])} pages, {len(lesson['points'])} points, "
              f"{lesson['duration']} seconds")


if __name__ == "__main__":
    main()
