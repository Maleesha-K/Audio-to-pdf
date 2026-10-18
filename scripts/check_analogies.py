"""Regression checks for safe analogy plans and persistent virtual pages."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.analogies import validate_plan  # noqa: E402

lesson = json.loads((ROOT / "data/lessons/sample-optimization/lesson.json").read_text(encoding="utf-8"))
hill = next(w for w in lesson["whiteboards"] if w["template"] == "hill" and w["after_source_page"] == 6)
references = [p for p in lesson["points"] if p.get("page_id") == hill["id"]]
assert len(references) >= 3, "Expected later hill references to reuse the persistent page"
assert lesson["lesson_pages"].index(hill) == next(i for i,p in enumerate(lesson["lesson_pages"]) if p["id"] == "pdf-6") + 1

unsafe = {"title":"<script>x</script>","template":"custom","confidence":.9,"commands":[
    {"kind":"script","at":0,"text":"bad"},
    {"kind":"point","at":999,"x":-5,"y":8,"label":"<b>safe</b>"},
]}
clean = validate_plan(unsafe, 10)
assert clean and len(clean["commands"]) == 1
assert clean["commands"][0]["at"] == 10 and clean["commands"][0]["x"] == 0 and clean["commands"][0]["y"] == 1
assert "<" not in clean["title"] and "<" not in clean["commands"][0]["label"]
print(f"Analogy checks passed: {len(lesson['whiteboards'])} persistent pages; hill reused {len(references)} times")
