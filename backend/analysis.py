"""Local transcription and conservative, editable audio-to-PDF alignment.

This baseline intentionally withholds a visual mark when it cannot find evidence.
Gemini can be added to refine candidate regions without changing the event format.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from .visuals import extract_visuals, choose_visual
from .analogies import add_analogy_pages
from .equation_focus import build_focus
from .grounding import ocr_words, build_document_map, align_document

STOP = set("""a an and are as at be because but by can could did do does for from had has have how i if in into is it its just like may might not of on or our so some that the their then there these they this those to up us was we were what when where which with you your right exactly really text paper source material topic main next slide one two three four five six seven eight nine ten first second about after before more most very all each every""".split())

TOPIC_CUES = ("main topic", "next topic", "subtopic", "moving to", "brings us to", "dive into")
VISUAL_CUES = ("visual", "slide", "figure", "graph", "diagram", "cartoon", "equation", "formula", "image", "shows", "labels")
ANALOGY_CUES = ("imagine", "analogy", "for example", "in our house", "like a soundboard", "like a heavy ball", "think of")


def tokenize(value: str) -> list[str]:
    value = value.lower().replace("decent", "descent").replace("rms prop", "rmsprop").replace("versus", "vs")
    return [x for x in re.findall(r"[a-z]+\d*|\d+", value) if x not in STOP and len(x) > 1]


def similarity(a: str, b: str) -> float:
    x, y = Counter(tokenize(a)), Counter(tokenize(b))
    if not x or not y:
        return 0.0
    common = set(x) & set(y)
    dot = sum(x[t] * y[t] for t in common)
    norm = math.sqrt(sum(v * v for v in x.values()) * sum(v * v for v in y.values()))
    return dot / norm if norm else 0.0


def analyze_pdf(pdf_path: Path, output_dir: Path, scale: float = 1.55) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered = pdfium.PdfDocument(str(pdf_path))
    pages = []
    with pdfplumber.open(pdf_path) as text_pdf:
        for index, (render_page, text_page) in enumerate(zip(rendered, text_pdf.pages), 1):
            image_name = f"page-{index}.png"
            render_page.render(scale=scale).to_pil().convert("RGB").save(output_dir / image_name, optimize=True)
            width, height = float(text_page.width), float(text_page.height)
            words = []
            for word in text_page.extract_words(x_tolerance=2, y_tolerance=3):
                value = word.get("text", "").strip()
                if not value:
                    continue
                words.append({
                    "text": value,
                    "x": round(float(word["x0"]) / width, 5),
                    "y": round(float(word["top"]) / height, 5),
                    "w": round((float(word["x1"]) - float(word["x0"])) / width, 5),
                    "h": round((float(word["bottom"]) - float(word["top"])) / height, 5),
                })
            text = text_page.extract_text() or ""
            quality = "embedded"
            # A rendered scan can have a few hidden/garbled glyphs yet no usable prose.
            if (len([w for w in words if re.search(r"[A-Za-z]{2,}", w["text"])]) < 7
                    and len(text_page.chars) < 35):
                try:
                    detected = ocr_words(output_dir / image_name)
                    if detected:
                        words = detected
                        text = " ".join(w["text"] for w in detected)
                        quality = "ocr"
                except Exception:
                    quality = "unreadable"
            title = next((line.strip() for line in text.splitlines() if line.strip()), f"Page {index}")
            pages.append({"number": index, "title": title[:100], "text": text, "words": words,
                          "width": width, "height": height, "image": image_name,
                          "text_quality":quality,"visuals": extract_visuals(text_page)})
    build_document_map(pages)
    return pages


def transcribe(audio_path: Path, model_dir: Path, model_name: str = "base.en") -> dict:
    from faster_whisper import WhisperModel

    model_dir.mkdir(parents=True, exist_ok=True)
    cached = (model_dir / f"models--Systran--faster-whisper-{model_name}").exists()
    try:
        model = WhisperModel(model_name, device="cpu", compute_type="int8",
                             download_root=str(model_dir), local_files_only=cached)
    except Exception:
        model = WhisperModel(model_name, device="cpu", compute_type="int8", download_root=str(model_dir))
    rows, info = model.transcribe(str(audio_path), beam_size=5, language="en", vad_filter=True, word_timestamps=True)
    segments = []
    for row in rows:
        segments.append({
            "start": round(row.start, 3), "end": round(row.end, 3), "text": row.text.strip(),
            "words": [{"start": round(w.start, 3) if w.start is not None else None,
                       "end": round(w.end, 3) if w.end is not None else None,
                       "word": w.word, "probability": round(w.probability, 3)} for w in row.words or []],
        })
    return {"duration_seconds": round(info.duration, 3), "model": model_name, "segments": segments}


def make_points(segments: list[dict], max_seconds: float = 18.0) -> list[dict]:
    points = []
    current = []
    for segment in segments:
        text = segment.get("text", "").strip()
        if not text:
            continue
        elapsed = segment["end"] - current[0]["start"] if current else 0
        should_break = current and (elapsed > max_seconds or
            any(cue in text.lower() for cue in TOPIC_CUES) or
            (elapsed >= 7 and re.search(r"[.!?]\s*$",current[-1]["text"])))
        if should_break:
            points.append(_merge(current))
            current = []
        current.append(segment)
    if current:
        points.append(_merge(current))
    for i in range(len(points) - 1):
        gap = points[i + 1]["start"] - points[i]["end"]
        if 0 < gap <= 2.5:
            points[i]["end"] = points[i + 1]["start"]
    return points


def _merge(rows: list[dict]) -> dict:
    text = " ".join(r["text"].strip() for r in rows)
    words = [w for row in rows for w in row.get("words", [])]
    return {"start": round(rows[0]["start"], 3), "end": round(rows[-1]["end"], 3),
            "text": text, "words": words}


def _reference_kind(text: str) -> str:
    lower = text.lower()
    if any(cue in lower for cue in TOPIC_CUES):
        return "source"
    if "graph" in lower and "bowl" in lower:
        return "figure"
    if any(cue in lower for cue in ("source material", "slide shows", "slide has", "source has")) and any(cue in lower for cue in ("graph", "diagram", "figure", "curve", "cartoon")):
        return "figure"
    if _analogy_sketch(text):
        return "analogy"
    if any(cue in lower for cue in ANALOGY_CUES):
        return "analogy"
    if any(cue in lower for cue in ("formula", "equation", "partial derivative", "sigma", "alpha", "beta")):
        return "equation"
    if any(cue in lower for cue in ("graph", "diagram", "visual", "cartoon", "image", "line", "curve")):
        return "figure"
    if any(cue in lower for cue in VISUAL_CUES):
        return "source"
    return "none"


def _analogy_sketch(text: str) -> dict | None:
    lower = text.lower()
    if "house" in lower and ("price" in lower or "square footage" in lower or "bedroom" in lower):
        return {"title": "House price example", "nodes": ["Area", "Bedrooms", "Location", "Predicted price"],
                "arrows": [[0, 3], [1, 3], [2, 3]]}
    if "soundboard" in lower or "recording studio" in lower:
        return {"title": "Mixing desk analogy", "nodes": ["Input 1", "Input 2", "Weights", "Output mix"],
                "arrows": [[0, 2], [1, 2], [2, 3]]}
    if ("bowl" in lower or "valley" in lower or "hill" in lower) and any(cue in lower for cue in ("imagine", "picturing", "like a", "analogy", "think of")):
        return {"title": "Finding a lower error", "nodes": ["Current point", "Downhill step", "Lower error"],
                "arrows": [[0, 1], [1, 2]]}
    return None


def _find_word_region(text: str, page: dict) -> tuple[list[float] | None, float]:
    target = set(tokenize(text))
    candidates = []
    for w in page["words"]:
        wt = tokenize(w["text"])
        if wt and wt[0] in target and len(wt[0]) >= 3:
            candidates.append(w)
    if not candidates:
        return None, 0.0
    # Prefer a nearby pair on the same line, then a single distinctive word.
    best = max(candidates, key=lambda w: len(w["text"]))
    neighbors = [w for w in candidates if abs(w["y"] - best["y"]) < .024 and abs(w["x"] - best["x"]) < .28]
    x0 = min(w["x"] for w in neighbors); x1 = max(w["x"] + w["w"] for w in neighbors)
    y1 = max(w["y"] + w["h"] for w in neighbors)
    region = [round(x0, 4), round(min(y1 + .004, .98), 4), round(max(x1 - x0, .025), 4), .007]
    strength = min(.8, .25 + .13 * len(neighbors))
    return region, strength


def _topic_anchor(text: str, pages: list[dict], previous: int) -> int | None:
    lower = text.lower().replace("decent", "descent").replace("rms prop", "rmsprop").replace("atom optimizer", "adam optimizer")
    if not any(cue in lower for cue in TOPIC_CUES):
        return None
    cues = list(re.finditer(r"(?:main topic|subtopic|moving to|brings us to|dive into)", lower))
    if not cues:
        return None
    tail = lower[cues[-1].end():].lstrip(" ,:!?")
    if tail.startswith("."):
        tail = tail[1:].lstrip()
    sentences = re.split(r"[.!?]", tail, maxsplit=2)
    focused = sentences[0]
    if focused.startswith("of ") and len(sentences) > 1:
        focused += " " + sentences[1]
    # A title announced by the speaker is stronger evidence than word overlap
    # with an arbitrary later slide. Prefer the first matching forward page.
    ranked = []
    for page in pages:
        title = page["title"].lower().replace("decent", "descent")
        title_words = tokenize(title)
        if not title_words or page["number"] < previous:
            continue
        phrase = " ".join(title_words)
        spoken = " ".join(tokenize(focused))
        if phrase in spoken:
            ranked.append((len(title_words), spoken.index(phrase), page["number"]))
    if not ranked:
        return None
    # Longest specific heading wins; duplicate headings choose the earliest page.
    ranked.sort(key=lambda row: (-row[0], row[1], row[2]))
    return ranked[0][2]


def align_points(points: list[dict], pages: list[dict]) -> list[dict]:
    if not pages:
        return []
    last_page = 1
    anchors = {}
    previous_anchor = 1
    for i, point in enumerate(points):
        anchor = _topic_anchor(point["text"], pages, previous_anchor)
        if anchor is not None:
            anchors[i] = anchor
            previous_anchor = anchor
    result = []
    for index, point in enumerate(points):
        text = point["text"]
        if index in anchors:
            last_page = anchors[index]
        following = [page for i, page in anchors.items() if i > index]
        upper_page = min(following) - 1 if following else len(pages)
        upper_page = max(last_page, upper_page)
        # Use context to choose a page, then the point itself to choose a visible region.
        context = " ".join(x["text"] for x in points[max(0, index - 1):min(len(points), index + 2)])
        raw = []
        candidate_pages = [pages[anchors[index] - 1]] if index in anchors else pages[last_page - 1: min(upper_page, last_page + 1)]
        for page in candidate_pages:
            score = .72 * similarity(context, page["text"]) + .28 * similarity(text, page["title"])
            if page["number"] == last_page:
                score += .085
            if page["number"] == last_page + 1:
                score += .015
            raw.append((score, page))
        raw.sort(key=lambda item: item[0], reverse=True)
        best_score, candidate = raw[0]
        second_score = raw[1][0] if len(raw) > 1 else 0
        topic_cue = any(cue in text.lower() for cue in TOPIC_CUES)
        clear_match = index in anchors or best_score >= .19 or (topic_cue and best_score >= .14)
        if clear_match:
            last_page = candidate["number"]
        else:
            candidate = pages[last_page - 1]
        kind = _reference_kind(text)
        sketch = None
        region, word_score = _find_word_region(text, candidate)
        visual = choose_visual(text, candidate, kind) if kind in ("equation", "figure") else None
        if kind in ("equation", "figure"):
            if visual and clear_match:
                action, region = "box", visual["region"]
                confidence = min(.78, best_score + .38)
            else:
                action, region, confidence = "hold", None, min(.55, best_score)
        elif kind == "source" and region and best_score >= .17:
            action = "underline"
            confidence = min(.78, best_score + word_score)
        elif region and best_score >= .22 and len(tokenize(text)) >= 3:
            action = "underline"
            confidence = min(.75, best_score + word_score)
        else:
            action = "hold"
            region = None
            confidence = min(.6, best_score)
        result.append({
            "id": f"p{index+1:04d}", "start": point["start"], "end": point["end"],
            "text": text, "words": point.get("words", []),
            "topic": candidate["title"] if clear_match else "Narration",
            "concept": _concept(text), "reference_kind": kind, "page": candidate["number"],
            "action": action, "region": region, "sketch": sketch,
            "confidence": round(confidence, 3), "needs_review": confidence < .7 or kind == "figure",
            "evidence": {"page_score": round(best_score, 3), "margin": round(best_score - second_score, 3)},
        })
    return result


def _concept(text: str) -> str:
    words = text.strip().split()
    cleaned = " ".join(words[:12])
    if len(words) > 12:
        cleaned += "..."
    return cleaned


def process(pdf_path: Path, audio_path: Path, lesson_dir: Path, model_dir: Path,
            supplied_transcript: dict | None = None, title: str | None = None) -> dict:
    pages = analyze_pdf(pdf_path, lesson_dir / "pages")
    transcript = supplied_transcript or transcribe(audio_path, model_dir)
    (lesson_dir / "transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
    from .gemini_budget import GeminiBudget
    points = align_document(make_points(transcript["segments"]), pages, model_dir, budget=GeminiBudget(lesson_dir))
    # Existing whiteboards remain readable; new lessons concentrate on source grounding.
    lesson_pages = [{"id":f"pdf-{p['number']}","kind":"pdf","source_page":p["number"],
                     "title":p["title"],"image":p["image"],"width":p["width"],"height":p["height"]}
                    for p in pages]
    whiteboards = []
    resolved_title = (title or "").strip()
    if not resolved_title or resolved_title.lower() == "source":
        first_title = pages[0]["title"].strip() if pages else ""
        resolved_title = first_title if first_title and not re.match(r"^page\s+\d+$", first_title, re.I) else pdf_path.stem
    lesson = {"id": lesson_dir.name, "title": resolved_title, "duration": transcript.get("duration_seconds", 0),
              "audio_file": audio_path.name, "pages": pages, "lesson_pages": lesson_pages,
              "whiteboards": whiteboards, "points": points, "grounding_version":2,
              "stats": {"page_count": len(pages), "transcript_segments": len(transcript["segments"]),
                        "lesson_page_count": len(lesson_pages), "whiteboard_count": len(whiteboards),
                        "points": len(points), "review_count": sum(p["needs_review"] for p in points)}}
    lesson["equation_focus"] = build_focus(lesson, pdf_path)
    lesson["stats"]["equation_focus_count"] = len(lesson["equation_focus"])
    lesson["stats"]["equation_focus_review_count"] = sum(e["needs_review"] for e in lesson["equation_focus"])
    (lesson_dir / "lesson.json").write_text(json.dumps(lesson, ensure_ascii=False), encoding="utf-8")
    return lesson
