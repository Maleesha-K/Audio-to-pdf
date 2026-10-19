from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analysis import process, analyze_pdf, make_points
from .analogies import validate_plan
from .equation_focus import build_focus
from .grounding import align_document
from .video_export import export_mp4, lesson_hash

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
LESSONS = ROOT / "data" / "lessons"
MODELS = ROOT / "data" / "models"
LESSONS.mkdir(parents=True, exist_ok=True)
MODELS.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Audio to PDF", version="0.1.0")
app.mount("/ui", StaticFiles(directory=str(FRONTEND), html=True), name="ui")
JOBS: dict[str, dict] = {}
EXPORT_JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
MAX_FILE_BYTES = 120 * 1024 * 1024


class PointEdit(BaseModel):
    page: int | None = None
    block_id: str | None = None
    action: str | None = None
    region: list[float] | None = Field(default=None, min_length=4, max_length=4)
    concept: str | None = None
    needs_review: bool | None = None


class WhiteboardEdit(BaseModel):
    title: str | None = None
    commands: list[dict] | None = None
    approved: bool | None = None
    remove_illustration: bool | None = None


class EquationFocusEdit(BaseModel):
    start: float | None = None
    end: float | None = None
    equation_id: str | None = None
    term_id: str | None = None
    region: list[float] | None = Field(default=None, min_length=4, max_length=4)
    needs_review: bool | None = None


def _lesson_dir(lesson_id: str) -> Path:
    if not lesson_id or not all(ch.isalnum() or ch == "-" for ch in lesson_id):
        raise HTTPException(400, "Invalid lesson identifier")
    path = LESSONS / lesson_id
    if path.is_dir():
        return path
    for candidate in LESSONS.iterdir():
        if candidate.is_dir() and (candidate / "lesson.json").exists():
            try:
                data = json.loads((candidate / "lesson.json").read_text(encoding="utf-8-sig"))
                if data.get("id") == lesson_id:
                    return candidate
            except Exception:
                pass
    raise HTTPException(404, "Lesson not found")


def _load_lesson(lesson_id: str) -> dict:
    lesson_dir = _lesson_dir(lesson_id)
    path = lesson_dir / "lesson.json"
    if not path.exists():
        raise HTTPException(409, "Lesson is still processing")
    lesson = json.loads(path.read_text(encoding="utf-8-sig"))
    lesson["id"] = lesson_dir.name
    if "lesson_pages" not in lesson:
        lesson["lesson_pages"] = [{"id":f"pdf-{p['number']}","kind":"pdf","source_page":p["number"],
            "title":p["title"],"image":p["image"],"width":p["width"],"height":p["height"]} for p in lesson["pages"]]
        lesson["whiteboards"] = []
    for point in lesson.get("points", []):
        point.setdefault("page_id", f"pdf-{point.get('page', 1)}")
    lesson.setdefault("equation_focus", [])
    lesson.setdefault("stats", {})["equation_focus_count"] = len(lesson["equation_focus"])
    lesson["stats"]["equation_focus_review_count"] = sum(e.get("needs_review",False) for e in lesson["equation_focus"])
    return lesson


def _save_lesson(lesson: dict) -> None:
    path = _lesson_dir(lesson["id"]) / "lesson.json"
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(lesson, ensure_ascii=False), encoding="utf-8-sig")
    temp.replace(path)


async def _save_upload(upload: UploadFile, destination: Path) -> None:
    size = 0
    with destination.open("wb") as out:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                destination.unlink(missing_ok=True)
                raise HTTPException(413, "A file exceeds the 120 MB MVP limit")
            out.write(chunk)


def _write_job_file(lesson_dir: Path, payload: dict) -> None:
    try:
        if lesson_dir.is_dir():
            (lesson_dir / "job.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8-sig")
    except Exception:
        pass


def _run_job(lesson_id: str, transcript_path: Path | None, title: str | None = None) -> None:
    lesson_dir = LESSONS / lesson_id
    job_title = (title or lesson_id).strip() or lesson_id
    def _update_stage(msg: str) -> None:
        payload = {"status": "processing", "message": msg, "title": job_title}
        with LOCK:
            JOBS[lesson_id] = payload
        _write_job_file(lesson_dir, payload)
    _update_stage("Extracting PDF slides & math formulas…")
    try:
        audio_path = next(lesson_dir.glob("audio.*"))
        supplied = json.loads(transcript_path.read_text(encoding="utf-8-sig")) if transcript_path else None
        result = process(lesson_dir / "source.pdf", audio_path, lesson_dir, MODELS, supplied, title=title, progress_cb=_update_stage)
        ready_payload = {"status": "ready", "message": "Lesson ready", "title": result.get("title", job_title), "points": len(result["points"])}
        with LOCK:
            JOBS[lesson_id] = ready_payload
        _write_job_file(lesson_dir, ready_payload)
    except Exception as exc:
        fail_payload = {"status": "failed", "message": str(exc)[:500], "title": job_title}
        with LOCK:
            JOBS[lesson_id] = fail_payload
        _write_job_file(lesson_dir, fail_payload)




def _run_alignment_rebuild(lesson_id: str) -> None:
    """Re-ground saved points locally without discarding manual edits or whiteboards."""
    try:
        with LOCK:
            lesson = _load_lesson(lesson_id)
        lesson_dir = _lesson_dir(lesson_id)
        pages = analyze_pdf(lesson_dir / "source.pdf", lesson_dir / "pages")
        transcript = json.loads((lesson_dir / "transcript.json").read_text(encoding="utf-8-sig"))
        from .gemini_budget import GeminiBudget
        generated = align_document(make_points(transcript.get("segments",[])), pages, MODELS, budget=GeminiBudget(lesson_dir))
        updated = _merge_rebuilt_points(lesson, generated, pages)
        lesson["pages"] = pages
        lesson["points"] = updated
        lesson["grounding_version"] = 2
        if pages and str(lesson.get("title", "")).strip().lower() in {"", "source"}:
            lesson["title"] = pages[0]["title"]
        for page in lesson["lesson_pages"]:
            if page["kind"] == "pdf":
                page["title"] = pages[page["source_page"]-1]["title"]
        # Rebuild equation focus and retain deliberate user corrections.
        corrections = [e for e in lesson.get("equation_focus",[]) if e.get("edited")]
        generated_focus = build_focus(lesson, lesson_dir / "source.pdf")
        for correction in corrections:
            generated_focus = [event for event in generated_focus if not
                               (event.get("point_id") == correction.get("point_id") and
                                abs(event["start"] - correction.get("source_start", correction["start"])) < .35)]
            generated_focus.append(correction)
        generated_focus.sort(key=lambda event: event["start"])
        lesson["equation_focus"] = generated_focus
        lesson["stats"]["review_count"] = sum(p.get("needs_review",False) for p in updated)
        lesson["stats"]["points"] = len(updated)
        lesson["stats"]["equation_focus_count"] = len(lesson["equation_focus"])
        lesson["stats"]["equation_focus_review_count"] = sum(e.get("needs_review",False) for e in lesson["equation_focus"])
        with LOCK:
            _save_lesson(lesson)
            JOBS[lesson_id] = {"status":"ready","message":"Alignment rebuilt", "points":len(updated)}
    except Exception as exc:
        with LOCK:
            JOBS[lesson_id] = {"status":"failed","message":str(exc)[:500]}


def _merge_rebuilt_points(lesson: dict, generated: list[dict], pages: list[dict]) -> list[dict]:
    """Carry edits by positive time overlap and validate source references."""
    old_points = lesson.get("points", [])
    old_blocks = {block["id"]: block for page in lesson.get("pages", [])
                  for block in page.get("blocks", [])}
    new_blocks = {page["number"]: page.get("blocks", []) for page in pages}

    def is_board(point: dict) -> bool:
        return (point.get("action") == "whiteboard" or bool(point.get("whiteboard_id")) or
                str(point.get("page_id", "")).startswith("wb-"))

    occupied = set()
    unmatched = []
    fields = ("page", "page_id", "action", "region", "concept", "reference_kind", "needs_review")
    for old in (point for point in old_points if point.get("edited") and not is_board(point)):
        ranked = sorted(((max(0., min(point["end"], old["end"]) - max(point["start"], old["start"])),
                          index, point) for index, point in enumerate(generated) if index not in occupied),
                        reverse=True)
        if not ranked or ranked[0][0] <= 0:
            retained = dict(old)
            retained["needs_review"] = True
            unmatched.append(retained)
            continue
        _, index, target = ranked[0]
        occupied.add(index)
        for field in fields:
            if field in old:
                target[field] = old[field]
        target["edited"] = True
        old_block = old_blocks.get(old.get("block_id"))
        same_page = new_blocks.get(target.get("page"), [])
        replacement = next((block for block in same_page if old_block and
                            block["text"].strip() == old_block["text"].strip()), None)
        target["block_id"] = replacement["id"] if replacement else None
        target["parent_block_id"] = replacement.get("parent_id") if replacement else None
        if old.get("block_id") and not replacement:
            target["needs_review"] = True

    boards = [dict(point) for point in old_points if is_board(point)]
    updated = sorted(generated + unmatched + boards, key=lambda point: point["start"])
    used_ids = set()
    for point in updated:
        original = str(point.get("id") or "point")
        candidate = original
        suffix = 2
        while candidate in used_ids:
            candidate = f"{original}-{suffix}"
            suffix += 1
        point["id"] = candidate
        used_ids.add(candidate)
    return updated


def _run_export(job_id: str, lesson_id: str, export_id: str) -> None:
    lesson_dir = LESSONS / lesson_id
    output = lesson_dir / "exports" / f"{export_id}.mp4"
    try:
        lesson = _load_lesson(lesson_id)
        def update(value: float):
            with LOCK:
                EXPORT_JOBS[job_id] = {"status":"processing","message":f"Rendering video… {round(value*100)}%",
                    "progress":round(value,3),"lesson_id":lesson_id,"export_id":export_id}
        export_mp4(lesson, lesson_dir, output, update)
        with LOCK:
            EXPORT_JOBS[job_id] = {"status":"ready","message":"MP4 ready","progress":1,
                "lesson_id":lesson_id,"export_id":export_id,"size_bytes":output.stat().st_size,
                "download_url":f"/api/lessons/{lesson_id}/exports/{export_id}.mp4"}
    except Exception as exc:
        with LOCK:
            EXPORT_JOBS[job_id] = {"status":"failed","message":str(exc)[:800],"progress":0,
                "lesson_id":lesson_id,"export_id":export_id}


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "project": "Gemini Notebook+ Annotator", "model": "gemini-2.5-flash"}


@app.get("/api/lessons")
def list_lessons():
    flagship_order = {
        "sample-optimization": 0,
        "c06d6f2733cf": 1,
        "340684620bb9": 2,
        "39e3634e87d8": 3,
        "9b1f46f4bacb": 4,
        "764e94c7cee1": 5,
        "eff13ddc461d": 6,
    }
    archived_test_runs = {"b4cba7ab007f", "658ebbdb704e", "bc4fc246efc9", "e06c8a8cf140", "179957e3d5ba"}
    rows = []
    with LOCK:
        active_jobs = {lid: dict(j) for lid, j in JOBS.items()}
    for lid, job in reversed(list(active_jobs.items())):
        lesson_dir = LESSONS / lid
        if lesson_dir.is_dir() and not (lesson_dir / "lesson.json").exists():
            st = job.get("status", "processing")
            if st in {"queued", "processing", "failed"}:
                rows.append({
                    "id": lid,
                    "title": job.get("title") or lid,
                    "status": st,
                    "status_message": job.get("message") or "Generating notebook…",
                    "duration": 0,
                    "stats": {"page_count": 0, "equation_focus_count": 0, "gemini_count": 0},
                    "is_flagship": False,
                    "is_duplicate": False,
                })
    seen_titles: set[str] = set()
    all_paths = sorted(LESSONS.iterdir(), key=lambda p: (flagship_order.get(p.name, 99), -p.stat().st_mtime))
    for path in all_paths:
        lesson_file = path / "lesson.json"
        if path.is_dir() and lesson_file.exists():
            data = json.loads(lesson_file.read_text(encoding="utf-8-sig"))
            stats = dict(data.get("stats") or {})
            stats["equation_focus_count"] = len(data.get("equation_focus") or [])
            stats["gemini_count"] = sum(
                1 for pt in data.get("points") or []
                if "gemini_disambiguation" in (pt.get("evidence") or {})
            )
            norm_title = str(data.get("title", "")).strip().lower()
            is_duplicate = (path.name in archived_test_runs or norm_title in seen_titles) and path.name not in flagship_order
            seen_titles.add(norm_title)
            rows.append({
                "id": path.name,
                "title": data["title"],
                "status": "ready",
                "duration": data["duration"],
                "stats": stats,
                "is_flagship": path.name in flagship_order,
                "is_duplicate": is_duplicate,
            })
        elif path.is_dir() and not lesson_file.exists() and (path / "source.pdf").exists() and path.name not in active_jobs:
            job_info = {}
            job_file = path / "job.json"
            if job_file.exists():
                try:
                    job_info = json.loads(job_file.read_text(encoding="utf-8-sig"))
                except Exception:
                    pass
            title = job_info.get("title") or path.name
            st = job_info.get("status", "processing")
            msg = job_info.get("message", "Processing in background…")
            if st == "processing":
                st = "failed"
                msg = "Interrupted by server restart. Click trash to remove."
            rows.append({
                "id": path.name,
                "title": title,
                "status": st,
                "status_message": msg,
                "duration": 0,
                "stats": {"page_count": 0, "equation_focus_count": 0, "gemini_count": 0},
                "is_flagship": False,
                "is_duplicate": False,
            })
    return rows



@app.post("/api/lessons", status_code=202)
async def create_lesson(background: BackgroundTasks, pdf: UploadFile = File(...),
                        audio: UploadFile = File(...), transcript: UploadFile | None = File(None)):
    if not pdf.filename or not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Choose a PDF file")
    audio_suffix = Path(audio.filename or "").suffix.lower()
    if audio_suffix not in {".mp3", ".m4a", ".wav", ".mp4", ".ogg", ".flac"}:
        raise HTTPException(400, "Choose an MP3, M4A, WAV, MP4, OGG, or FLAC audio file")
    lesson_id = uuid.uuid4().hex[:12]
    lesson_dir = LESSONS / lesson_id
    lesson_dir.mkdir()
    pdf_path = lesson_dir / "source.pdf"
    audio_path = lesson_dir / f"audio{audio_suffix}"
    await _save_upload(pdf, pdf_path)
    with pdf_path.open("rb") as pdf_file:
        signature = pdf_file.read(5)
    if signature != b"%PDF-":
        shutil.rmtree(lesson_dir)
        raise HTTPException(400, "The uploaded PDF is invalid")
    await _save_upload(audio, audio_path)
    transcript_path = None
    if transcript is not None and transcript.filename:
        transcript_path = lesson_dir / "supplied-transcript.json"
        await _save_upload(transcript, transcript_path)
        try:
            supplied = json.loads(transcript_path.read_text(encoding="utf-8-sig"))
            if not isinstance(supplied.get("segments"), list):
                raise ValueError("Missing segments")
        except (ValueError, UnicodeError):
            shutil.rmtree(lesson_dir)
            raise HTTPException(400, "Transcript JSON must contain a segments array")
    upload_title = Path(pdf.filename).stem
    job_payload = {"status": "queued", "message": "Preparing notebook…", "title": upload_title}
    with LOCK:
        JOBS[lesson_id] = job_payload
    _write_job_file(lesson_dir, job_payload)
    background.add_task(_run_job, lesson_id, transcript_path, upload_title)
    return {"id": lesson_id, "title": upload_title, "status": "queued", "message": "Preparing notebook…"}



@app.get("/api/jobs/{lesson_id}")
def job_status(lesson_id: str):
    lesson_dir = _lesson_dir(lesson_id)
    with LOCK:
        job = JOBS.get(lesson_id)
    if job:
        return job
    job_file = lesson_dir / "job.json"
    if job_file.exists():
        try:
            return json.loads(job_file.read_text(encoding="utf-8-sig"))
        except Exception:
            pass
    if (lesson_dir / "lesson.json").exists():
        return {"status": "ready", "message": "Lesson ready"}
    if (lesson_dir / "source.pdf").exists():
        return {"status": "processing", "message": "Processing in background…"}
    return {"status": "unknown", "message": "No active processing job"}


@app.get("/api/lessons/{lesson_id}")
def get_lesson(lesson_id: str):
    lesson = _load_lesson(lesson_id)
    lesson_dir = _lesson_dir(lesson_id)
    # Large PDF text and word coordinates stay server-side. The player receives page metadata and events.
    response = {key: value for key, value in lesson.items() if key != "pages"}
    response["pages"] = [{key: page[key] for key in ("number", "title", "image", "width", "height")}
                         for page in lesson["pages"]]
    response["equations"] = {str(page["number"]): [v for v in page.get("visuals",[]) if v.get("kind") == "equation"]
                             for page in lesson["pages"]}
    response["document_blocks"] = {str(page["number"]): page.get("blocks",[]) for page in lesson["pages"]}
    response["points"] = [{key: value for key, value in point.items() if key != "words"}
                          for point in lesson["points"]]
    transcript_path = lesson_dir / "transcript.json"
    if transcript_path.exists():
        try:
            tr = json.loads(transcript_path.read_text(encoding="utf-8-sig"))
            response["captions"] = [
                {"start": s["start"], "end": s["end"], "text": s.get("text", "").strip()}
                for s in tr.get("segments", []) if s.get("text", "").strip()
            ]
        except Exception:
            response["captions"] = []
    else:
        response["captions"] = []
    return response


def _safe_remove_dir(target: Path, retries: int = 6, delay: float = 0.25) -> None:
    import gc, time
    gc.collect()
    for attempt in range(retries):
        try:
            if target.exists():
                shutil.rmtree(target)
            return
        except (PermissionError, OSError):
            gc.collect()
            time.sleep(delay)
    if target.exists():
        try:
            trash = target.parent / f".trash_{target.name}_{int(time.time())}"
            target.rename(trash)
            shutil.rmtree(trash, ignore_errors=True)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)


@app.delete("/api/lessons/{lesson_id}")
def delete_lesson(lesson_id: str):
    lesson_dir = _lesson_dir(lesson_id)
    with LOCK:
        try:
            _safe_remove_dir(lesson_dir)
            JOBS.pop(lesson_id, None)
            JOBS.pop(lesson_dir.name, None)
            stale_exports = [k for k, v in EXPORT_JOBS.items() if v.get("lesson_id") in (lesson_id, lesson_dir.name)]
            for k in stale_exports:
                EXPORT_JOBS.pop(k, None)
        except Exception as exc:
            raise HTTPException(500, f"Could not delete notebook: {exc}")
    return {"ok": True, "id": lesson_id}



@app.post("/api/lessons/{lesson_id}/alignment/rebuild", status_code=202)
def rebuild_alignment(lesson_id: str, background: BackgroundTasks):
    _load_lesson(lesson_id)
    with LOCK:
        if JOBS.get(lesson_id,{}).get("status") == "processing":
            raise HTTPException(409,"Alignment is already processing")
        JOBS[lesson_id] = {"status":"processing","message":"Rebuilding PDF grounding locally"}
    background.add_task(_run_alignment_rebuild,lesson_id)
    return {"id":lesson_id,"status":"processing"}


AUDIO_MIME = {".m4a": "audio/mp4", ".mp4": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav",
              ".ogg": "audio/ogg", ".flac": "audio/flac"}


@app.get("/api/lessons/{lesson_id}/audio")
def get_audio(lesson_id: str):
    lesson_dir = _lesson_dir(lesson_id)
    matches = sorted(lesson_dir.glob("audio.*"))
    path = matches[0] if matches else lesson_dir / _load_lesson(lesson_id)["audio_file"]
    # Slim Linux images lack /etc/mime.types, so .m4a would otherwise be served as octet-stream.
    return FileResponse(path, media_type=AUDIO_MIME.get(path.suffix.lower(), "application/octet-stream"))


@app.get("/api/lessons/{lesson_id}/pages/{page_number}")
def get_page(lesson_id: str, page_number: int):
    if page_number < 1:
        raise HTTPException(404, "Page not found")
    page_path = _lesson_dir(lesson_id) / "pages" / f"page-{page_number}.png"
    if not page_path.is_file():
        raise HTTPException(404, "Page not found")
    return FileResponse(page_path, media_type="image/png")


@app.get("/api/lessons/{lesson_id}/illustrations/{filename}")
def get_illustration(lesson_id: str, filename: str):
    if len(filename) != 24 or not filename.endswith(".jpg") or not all(c in "0123456789abcdef" for c in filename[:-4]):
        raise HTTPException(404, "Illustration not found")
    lesson = _load_lesson(lesson_id)
    if not any(board.get("illustration") == filename for board in lesson.get("whiteboards", [])):
        raise HTTPException(404, "Illustration not found")
    path = _lesson_dir(lesson_id) / "illustrations" / filename
    if not path.exists():
        raise HTTPException(404, "Illustration not found")
    return FileResponse(path, media_type="image/jpeg")


@app.patch("/api/lessons/{lesson_id}/points/{point_id}")
def edit_point(lesson_id: str, point_id: str, edit: PointEdit):
    with LOCK:
        lesson = _load_lesson(lesson_id)
        point = next((p for p in lesson["points"] if p["id"] == point_id), None)
        if point is None:
            raise HTTPException(404, "Point not found")
        changes = edit.model_dump(exclude_unset=True)
        if "page" in changes and (changes["page"] is None or not 1 <= changes["page"] <= len(lesson["pages"])):
            raise HTTPException(400, "Page is out of range")
        if "action" in changes and changes["action"] not in {"hold", "underline", "box"}:
            raise HTTPException(400, "Invalid action")
        if "block_id" in changes and changes["block_id"]:
            target_page=changes.get("page") or point.get("page",1)
            block=next((b for b in lesson["pages"][target_page-1].get("blocks",[])
                        if b["id"]==changes["block_id"]),None)
            if not block:
                raise HTTPException(400,"Block does not belong to the selected page")
            changes["parent_block_id"]=block.get("parent_id")
        elif "block_id" in changes:
            changes["parent_block_id"]=None
        if "region" in changes and changes["region"] is not None:
            if any(not 0 <= value <= 1 for value in changes["region"]):
                raise HTTPException(400, "Region values must be between zero and one")
        point.update(changes)
        if point.get("action") != "whiteboard":
            point["page_id"] = f"pdf-{point.get('page', 1)}"
        point["edited"] = True
        _save_lesson(lesson)
        return point


@app.patch("/api/lessons/{lesson_id}/whiteboards/{board_id}")
def edit_whiteboard(lesson_id: str, board_id: str, edit: WhiteboardEdit):
    with LOCK:
        lesson = _load_lesson(lesson_id)
        board = next((b for b in lesson.get("whiteboards", []) if b["id"] == board_id), None)
        if board is None:
            raise HTTPException(404, "Whiteboard not found")
        changes = edit.model_dump(exclude_unset=True)
        if "title" in changes:
            if not isinstance(changes["title"], str) or not changes["title"].strip():
                raise HTTPException(400, "Title is required")
            board["title"] = changes["title"].strip()[:100]
        if "commands" in changes:
            commands = changes["commands"]
            if not isinstance(commands, list) or len(commands) > 12 or not commands:
                raise HTTPException(400, "Provide one to twelve drawing steps")
            duration = max(0.1, float(board["end"])-float(board["start"]))
            for item in commands:
                try:
                    at = float(item.get("at", -1)) if isinstance(item, dict) else -1
                    valid_coords = all(0 <= float(item[key]) <= 1 for key in
                                       ("x","y","x1","y1","x2","y2","w","h") if key in item)
                except (TypeError, ValueError):
                    raise HTTPException(400, "Invalid drawing number")
                if not 0 <= at <= duration:
                    raise HTTPException(400, "A drawing time is outside the analogy")
                if not valid_coords:
                    raise HTTPException(400, "Drawing coordinates must be between zero and one")
            safe = validate_plan({"title":board["title"],"template":board.get("template","custom"),
                                  "confidence":board.get("confidence",1),"commands":commands}, duration)
            if not safe or len(safe["commands"]) != len(commands):
                raise HTTPException(400, "Invalid drawing steps")
            board["commands"] = safe["commands"]
        if changes.get("remove_illustration"):
            board["illustration"] = None
            board["visual_mode"] = "drawing"
            board["review_status"] = "pending"
            for point in lesson["points"]:
                if point.get("whiteboard_id") == board_id:
                    point["needs_review"] = True
        if "approved" in changes and changes["approved"] is not None:
            board["review_status"] = "approved" if changes["approved"] else "pending"
            for point in lesson["points"]:
                if point.get("whiteboard_id") == board_id:
                    point["needs_review"] = not changes["approved"]
        lesson["stats"]["review_count"] = sum(p.get("needs_review", False) for p in lesson["points"])
        page = next((p for p in lesson["lesson_pages"] if p["id"] == board_id), None)
        if page is not None:
            page.update(board)
        _save_lesson(lesson)
        return board


@app.post("/api/lessons/{lesson_id}/equation-focus/rebuild")
def rebuild_equation_focus(lesson_id: str):
    """Local only. Keep all manually corrected cues across a rebuild."""
    with LOCK:
        lesson = _load_lesson(lesson_id)
        corrections = [event for event in lesson["equation_focus"] if event.get("edited")]
        generated = build_focus(lesson, _lesson_dir(lesson_id) / "source.pdf")
        for correction in corrections:
            generated = [event for event in generated if not
                         (event.get("point_id") == correction.get("point_id") and
                          abs(event["start"] - correction.get("source_start", correction["start"])) < .35)]
            generated.append(correction)
        generated.sort(key=lambda event: event["start"])
        used = set()
        for event in generated:
            if event['id'] in used:
                event['id'] = f"ef-rebuilt-{len(used)+1:04d}"
            used.add(event['id'])
        lesson["equation_focus"] = generated
        lesson["stats"]["equation_focus_count"] = len(generated)
        lesson["stats"]["equation_focus_review_count"] = sum(e.get("needs_review",False) for e in generated)
        _save_lesson(lesson)
        return {"events": generated, "count": len(generated), "preserved_corrections": len(corrections)}


@app.patch("/api/lessons/{lesson_id}/equation-focus/{event_id}")
def edit_equation_focus(lesson_id: str, event_id: str, edit: EquationFocusEdit):
    with LOCK:
        lesson = _load_lesson(lesson_id)
        event = next((row for row in lesson["equation_focus"] if row["id"] == event_id), None)
        if event is None: raise HTTPException(404, "Equation cue not found")
        changes = edit.model_dump(exclude_unset=True)
        start = changes.get("start", event["start"])
        end = changes.get("end", event["end"])
        if start is None or end is None or not 0 <= start < end <= lesson["duration"] + 1:
            raise HTTPException(400, "Cue times are out of range")
        page = lesson["pages"][event["page"]-1]
        equation_id = changes.get("equation_id", event["equation_id"])
        equation = next((v for v in page.get("visuals", []) if v.get("id") == equation_id), None)
        if not equation: raise HTTPException(400, "Equation is not on this page")
        term_id = changes.get("term_id", event["term_id"])
        term = next((t for t in equation.get("terms", []) if t["id"] == term_id), None)
        if not term and term_id != "whole": raise HTTPException(400, "Term is not in this equation")
        region = changes.get("region", (term or equation)["region"])
        if region is None or any(v is None or not 0 <= v <= 1 for v in region) or region[2] <= 0 or region[3] <= 0 or region[0]+region[2] > 1 or region[1]+region[3] > 1:
            raise HTTPException(400, "Invalid target region")
        event.setdefault("source_start", event["start"])
        event.update(changes)
        event.update({"start":round(start,3),"end":round(end,3),"equation_id":equation_id,
                      "term_id":term_id,"region":region,"equation_region":equation["region"],"edited":True})
        lesson["equation_focus"].sort(key=lambda row: row["start"])
        lesson["stats"]["equation_focus_review_count"] = sum(e.get("needs_review",False) for e in lesson["equation_focus"])
        _save_lesson(lesson)
        return event


@app.get("/api/lessons/{lesson_id}/export.json")
def export_timeline(lesson_id: str):
    lesson = _load_lesson(lesson_id)
    return {"id": lesson_id, "title": lesson["title"], "duration": lesson["duration"], "lesson_pages":lesson["lesson_pages"],
            "whiteboards":lesson.get("whiteboards",[]), "points": lesson["points"],
            "equation_focus":lesson["equation_focus"]}


@app.post("/api/lessons/{lesson_id}/exports", status_code=202)
def create_export(lesson_id: str, background: BackgroundTasks):
    lesson = _load_lesson(lesson_id)
    export_id = lesson_hash(lesson)
    output = _lesson_dir(lesson_id) / "exports" / f"{export_id}.mp4"
    job_id = f"{lesson_id}-{export_id}"
    if output.exists():
        result = {"status":"ready","message":"Using cached MP4","progress":1,"lesson_id":lesson_id,
                  "export_id":export_id,"size_bytes":output.stat().st_size,
                  "download_url":f"/api/lessons/{lesson_id}/exports/{export_id}.mp4"}
        with LOCK: EXPORT_JOBS[job_id] = result
        return {"job_id":job_id, **result}
    with LOCK:
        current = EXPORT_JOBS.get(job_id)
        if current and current["status"] in {"queued","processing"}:
            return {"job_id":job_id, **current}
        EXPORT_JOBS[job_id] = {"status":"queued","message":"MP4 export queued","progress":0,
                               "lesson_id":lesson_id,"export_id":export_id}
    background.add_task(_run_export, job_id, lesson_id, export_id)
    return {"job_id":job_id, **EXPORT_JOBS[job_id]}


@app.get("/api/exports/{job_id}")
def export_status(job_id: str):
    with LOCK: job = EXPORT_JOBS.get(job_id)
    if not job: raise HTTPException(404, "Export job not found")
    return job


@app.get("/api/lessons/{lesson_id}/exports/{export_id}.mp4")
def download_export(lesson_id: str, export_id: str):
    if not export_id or not all(ch in "0123456789abcdef" for ch in export_id):
        raise HTTPException(400, "Invalid export identifier")
    lesson = _load_lesson(lesson_id)
    if lesson_hash(lesson) != export_id:
        raise HTTPException(409, "Lesson changed; create a new export")
    path = _lesson_dir(lesson_id) / "exports" / f"{export_id}.mp4"
    if not path.exists(): raise HTTPException(404, "Export not ready")
    return FileResponse(path, media_type="video/mp4", filename=f"{lesson['title']}-compact.mp4")
