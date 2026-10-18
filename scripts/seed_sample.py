"""Load the supplied optimization lecture as an immediately playable sample."""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.analysis import process  # noqa: E402

SAMPLE = ROOT / "samples"
TARGET = ROOT / "data" / "lessons" / "sample-optimization"


def main():
    TARGET.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SAMPLE / "optimization.pdf", TARGET / "source.pdf")
    shutil.copy2(SAMPLE / "optimization.m4a", TARGET / "audio.m4a")
    transcript = json.loads((SAMPLE / "optimization-transcript.json").read_text(encoding="utf-8"))
    lesson = process(TARGET / "source.pdf", TARGET / "audio.m4a", TARGET, ROOT / "data" / "models",
                     transcript, title="L05 - Optimization for Neural Networks")
    from backend.app import rebuild_equation_focus
    rebuild_equation_focus("sample-optimization")
    print(f"Sample ready: {lesson['id']} | {len(lesson['pages'])} pages | {len(lesson['points'])} points")
    print(f"Review points: {lesson['stats']['review_count']}")


if __name__ == "__main__":
    main()
