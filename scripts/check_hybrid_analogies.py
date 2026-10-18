"""Focused checks for grounded analogy timing, image caching, and shared rendering."""
import json
import os
import sys
import tempfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.analogies import add_analogy_pages, generate_illustration, validate_storyboard  # noqa: E402
from backend.video_export import FrameRenderer  # noqa: E402
from backend import app as server  # noqa: E402


point = {"id":"p1","start":10.0,"end":16.0,
         "text":"Imagine walking down a hill toward a lower error point.",
         "words":[{"word":word,"start":10+i*.5} for i,word in enumerate(
             "Imagine walking down a hill toward a lower error point".split())],
         "page":1,"action":"hold","needs_review":False}
story = {"classification":"drawable_analogy","concept":"Lower error","confidence":.91,
         "evidence":"walking down a hill","title":"Walk downhill","template":"hill",
         "commands":[{"kind":"point","cue":"walking down","x":.8,"y":.3,"label":"Start"},
                     {"kind":"arrow","cue":"lower error","x1":.8,"y1":.3,"x2":.4,"y2":.7,"label":"Downhill"}]}
clean = validate_storyboard(story, point)
assert clean and [step["at"] for step in clean["commands"]] == [0.5, 3.5]
assert clean["timing_source"] == "local_words"
assert validate_storyboard({**story,"commands":[{"kind":"script","cue":"walking"}]},point) is None
assert validate_storyboard({**story,"commands":[{"kind":"point","cue":"walking","x":1.2}]},point) is None
assert validate_storyboard({**story,"commands":[{"kind":"point","cue":"invented","x":.4}]},point) is None
assert validate_storyboard({**story,"confidence":.3},point)["classification"] == "uncertain"

with patch.dict(os.environ, {"GEMINI_API_KEY":""}):
    pages = [{"number":1,"title":"Gradient descent","image":"page-1.png","width":100,"height":100}]
    rows, boards = add_analogy_pages([dict(point)], pages)
    assert len(boards)==1 and rows[1]["id"]==boards[0]["id"]
    assert boards[0]["illustration"] is None
    unsure = dict(point,text="Imagine an unfamiliar machine and what it means.")
    uncertain_rows, uncertain_boards = add_analogy_pages([unsure], pages)
    assert not uncertain_boards and unsure["needs_review"] and unsure["action"]=="hold"

with tempfile.TemporaryDirectory(dir=ROOT / "data") as tmp:
    directory=Path(tmp)
    image=Image.new("RGB",(640,360),(230,180,40))
    data=BytesIO();image.save(data,"PNG")
    fake=SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_:SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(
            inline_data=SimpleNamespace(data=data.getvalue()))]))])))
    image_plan={"visual_mode":"illustration","image_prompt":"A simple mountain landscape"}
    with patch.dict(os.environ,{"GEMINI_API_KEY":"test"}), patch("google.genai.Client",return_value=fake) as client:
        filename=generate_illustration(image_plan,directory)
        assert filename and (directory/"illustrations"/filename).exists()
        assert generate_illustration(image_plan,directory)==filename
        assert client.call_count==1, "Second request should use the cached image"
    board={"id":"wb-1","kind":"whiteboard","title":"Illustrated analogy","start":10,"end":16,
           "commands":[],"illustration":filename}
    lesson={"lesson_pages":[{"id":"pdf-1","kind":"pdf"},board],"whiteboards":[board],
            "points":[{"start":10,"page_id":"wb-1"}]}
    frame=FrameRenderer(lesson,directory).frame(11)
    assert frame.getpixel((640,360))[0]>180, "MP4 renderer should include the saved illustration"

    lesson_dir=directory/"trial";lesson_dir.mkdir()
    (lesson_dir/"illustrations").mkdir()
    (lesson_dir/"illustrations"/filename).write_bytes((directory/"illustrations"/filename).read_bytes())
    record={"id":"trial","title":"Test lesson","duration":16,"audio_file":"audio.m4a",
            "pages":[{"number":1,"title":"Gradient descent","image":"page-1.png","width":100,"height":100}],
            "lesson_pages":[{"id":"pdf-1","kind":"pdf","source_page":1,"title":"Gradient descent"},board],
            "whiteboards":[board],"points":[{"id":"p1","start":10,"end":16,"page":1,
                "page_id":"wb-1","whiteboard_id":"wb-1","action":"whiteboard","needs_review":True}],
            "stats":{"review_count":1}}
    (lesson_dir/"lesson.json").write_text(json.dumps(record),encoding="utf8")
    with patch.object(server,"LESSONS",directory):
        assert Path(server.get_illustration("trial",filename).path) == lesson_dir/"illustrations"/filename
        saved_board=server.edit_whiteboard("trial","wb-1",server.WhiteboardEdit(**{
            "title":"Checked analogy","commands":[{"kind":"text","at":1,"x":.5,"y":.5,"text":"Lower error"}],
            "approved":True}))
        assert saved_board["title"]=="Checked analogy"
        saved=json.loads((lesson_dir/"lesson.json").read_text(encoding="utf8"))
        assert saved["whiteboards"][0]["title"]==saved["lesson_pages"][1]["title"]=="Checked analogy"
        assert saved["stats"]["review_count"]==0
        server.edit_whiteboard("trial","wb-1",server.WhiteboardEdit(remove_illustration=True,approved=False))
        saved=json.loads((lesson_dir/"lesson.json").read_text(encoding="utf8"))
        assert saved["lesson_pages"][1]["illustration"] is None and saved["stats"]["review_count"]==1

print("Hybrid analogy checks passed: word cues, uncertain cases, image cache, MP4 frame, review API")
