"""Check announced sections against the supplied optimization lecture."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lesson = json.loads((ROOT / "data/lessons/sample-optimization/lesson.json").read_text(encoding="utf-8"))
expected = {
    180: 3,   # Feed forward network
    404: 4,   # Adjusting the weights
    561: 6,   # Gradient descent
    655: 10,  # Update step
    703: 12,  # Learning parameters
    743: 13,  # Stochastic gradient descent
    791: 14,  # Mini batch
    813: 15,  # Momentum
    863: 22,  # RMSprop
    904: 23,  # Adam
    941: 24,  # Learning rate decay
    991: 25,  # Hyperparameter tuning
    1019: 26, # Grid vs random search
}
failures = []
for second, page in expected.items():
    point = min(lesson["points"], key=lambda item: abs(item["start"] - second))
    if abs(point["start"] - second) > 2 or point["page"] != page:
        failures.append(f"{second}s expected page {page}, got {point['page']} at {point['start']}s")
print(f"Checked {len(expected)} announced section anchors; {len(failures)} failed.")
for failure in failures:
    print(failure)
raise SystemExit(bool(failures))
