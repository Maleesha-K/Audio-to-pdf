"""Persistent per-lesson Gemini budget for analogy planning and illustrations."""
from __future__ import annotations

import json
import threading
from pathlib import Path

MAX_CALLS = 8
CALL_LOCK = threading.Lock()  # serialize requests across lessons on a free-tier project


class GeminiBudget:
    def __init__(self, lesson_dir: Path | None):
        self.path = lesson_dir / "gemini-budget.json" if lesson_dir else None
        self.data = {"calls": 0, "quota_exhausted": False, "plan_cache": {}}
        if self.path and self.path.is_file():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                self.data.update({key: saved[key] for key in self.data if key in saved})
            except (ValueError, OSError, TypeError):
                pass

    def _save(self):
        if self.path:
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.path)

    def cached_plan(self, key: str):
        return self.data["plan_cache"].get(key)

    def save_plan(self, key: str, value: str):
        self.data["plan_cache"][key] = value
        self._save()

    def run(self, operation):
        with CALL_LOCK:
            if self.data["calls"] >= MAX_CALLS or self.data["quota_exhausted"]:
                return None
            self.data["calls"] += 1
            self._save()  # reserve before the request, including failures
            try:
                return operation()
            except Exception as exc:
                message = str(exc).lower()
                if "429" in message or "resource_exhausted" in message or "quota" in message:
                    self.data["quota_exhausted"] = True
                    self._save()
                return None
