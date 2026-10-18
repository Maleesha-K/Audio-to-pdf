"""Render a lesson timeline directly into a compact H.264 MP4."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg
from .equation_focus import active_focus

WIDTH, HEIGHT, FPS, RENDER_FPS = 1280, 720, 24, 8
ORANGE = (249, 115, 22, 255)
INK = (28, 48, 78, 255)
MUTED = (75, 100, 135, 255)


def lesson_hash(lesson: dict) -> str:
    payload = {"duration":lesson.get("duration"), "points":lesson.get("points",[]),
               "lesson_pages":lesson.get("lesson_pages",[]), "whiteboards":lesson.get("whiteboards",[]),
               "equation_focus":lesson.get("equation_focus",[])}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def _font(size: int):
    for name in ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf"):
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def _active_point(points: list[dict], second: float) -> dict | None:
    active = None
    for point in points:
        if point["start"] <= second:
            active = point
        else:
            break
    return active


class FrameRenderer:
    def __init__(self, lesson: dict, lesson_dir: Path):
        self.lesson, self.lesson_dir = lesson, lesson_dir
        self.page_map = {p["id"]:p for p in lesson["lesson_pages"]}
        self.boards = {p["id"]:p for p in lesson.get("whiteboards", [])}
        self.base_cache: dict[str, tuple[Image.Image, tuple[int,int,int,int]]] = {}

    def _pdf_base(self, page: dict):
        if page["id"] in self.base_cache:
            return self.base_cache[page["id"]]
        source = Image.open(self.lesson_dir / "pages" / page["image"]).convert("RGB")
        scale = min(WIDTH/source.width, HEIGHT/source.height)
        size = (round(source.width*scale), round(source.height*scale))
        fitted = source.resize(size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (WIDTH, HEIGHT), (225,230,238))
        left, top = (WIDTH-size[0])//2, (HEIGHT-size[1])//2
        canvas.paste(fitted, (left,top))
        result = canvas, (left,top,size[0],size[1])
        self.base_cache[page["id"]] = result
        return result

    def frame(self, second: float) -> Image.Image:
        point = _active_point(self.lesson["points"], second)
        page_id = (point or {}).get("page_id", "pdf-1")
        page = self.page_map.get(page_id, self.page_map["pdf-1"])
        if page["kind"] == "whiteboard":
            return self._whiteboard(page, second)
        base, box = self._pdf_base(page)
        canvas = base.copy().convert("RGBA")
        overlay = Image.new("RGBA", canvas.size, (0,0,0,0)); draw = ImageDraw.Draw(overlay)
        left, top, width, height = box
        focused_point_ids = {event['point_id'] for event in self.lesson.get('equation_focus',[])}
        focus = active_focus(self.lesson, second, page_id)
        for item in self.lesson["points"]:
            if item.get("page_id", f"pdf-{item.get('page',1)}") != page_id or item["start"] > second:
                continue
            region = item.get("region")
            if not region or item.get("action") not in {"underline","box"}:
                continue
            if self.lesson.get("grounding_version",0) >= 2 and item.get("needs_review"):
                continue
            if item['action'] == 'box' and (item['id'] in focused_point_ids or focus):
                continue
            x,y,w,h = region
            xy = (left+x*width, top+y*height, left+(x+w)*width, top+(y+h)*height)
            color = ORANGE if item is point else (249,115,22,125)
            if item["action"] == "box": draw.rectangle(xy, outline=color, width=3)
            else: draw.line((xy[0],xy[1],xy[2],xy[1]), fill=color, width=3)
        if focus and focus.get('region'):
            x,y,w,h=focus['region']
            draw.rectangle((left+x*width,top+y*height,left+(x+w)*width,top+(y+h)*height),
                           outline=ORANGE,width=3)
        elif point and point.get('reference_kind') == 'equation' and point.get('id') in focused_point_ids:
            # If no term can be grounded, retain one whole-equation target.
            region = point.get('region') if point.get('action') == 'box' else None
            if region:
                x,y,w,h=region
                draw.rectangle((left+x*width,top+y*height,left+(x+w)*width,top+(y+h)*height),
                               outline=ORANGE,width=3)
        return Image.alpha_composite(canvas, overlay).convert("RGB")

    def _whiteboard(self, board: dict, second: float) -> Image.Image:
        canvas = Image.new("RGB", (WIDTH,HEIGHT), "white")
        filename = board.get("illustration")
        if filename:
            path = self.lesson_dir / "illustrations" / filename
            if path.is_file():
                with Image.open(path) as source:
                    art = source.convert("RGB")
                    art.thumbnail((round(WIDTH*.82), round(HEIGHT*.72)), Image.Resampling.LANCZOS)
                left = round(WIDTH*.09 + (WIDTH*.82-art.width)/2)
                top = round(HEIGHT*.135 + (HEIGHT*.72-art.height)/2)
                canvas.paste(art, (left, top))
        draw = ImageDraw.Draw(canvas)
        draw.text((45,28), board["title"], fill=INK, font=_font(28))
        draw.text((45,65), "ILLUSTRATIVE WHITEBOARD", fill=MUTED, font=_font(14))
        elapsed = max(0, second-board["start"])
        for command in board["commands"]:
            if elapsed < command["at"]: continue
            progress = min(1, (elapsed-command["at"])/.8)
            self._command(draw, command, progress)
        return canvas

    def _command(self, draw: ImageDraw.ImageDraw, cmd: dict, progress: float):
        sx, sy = WIDTH*.82, HEIGHT*.72; ox, oy = WIDTH*.09, HEIGHT*.15
        pt = lambda x,y: (ox+x*sx, oy+y*sy)
        kind = cmd["kind"]
        if kind == "curve":
            points = [pt(*p) for p in cmd["points"]]
            count = max(2, min(len(points), math.ceil(len(points)*progress)))
            draw.line(points[:count], fill=INK, width=5, joint="curve")
        elif kind in {"line","arrow"}:
            a=pt(cmd.get("x1",0),cmd.get("y1",0)); b=pt(cmd.get("x2",1),cmd.get("y2",1))
            end=(a[0]+(b[0]-a[0])*progress,a[1]+(b[1]-a[1])*progress)
            draw.line((a,end),fill=INK,width=5)
            if kind=="arrow" and progress>.9:
                angle=math.atan2(b[1]-a[1],b[0]-a[0]); length=18
                wings=[(b[0]-length*math.cos(angle-z),b[1]-length*math.sin(angle-z)) for z in (.55,-.55)]
                draw.line((wings[0],b,wings[1]),fill=INK,width=5)
        elif kind == "point":
            x,y=pt(cmd.get("x",.5),cmd.get("y",.5)); r=9
            draw.ellipse((x-r,y-r,x+r,y+r),fill=ORANGE)
        elif kind in {"rectangle","ellipse"}:
            x,y=pt(cmd.get("x",.1),cmd.get("y",.1)); w=cmd.get("w",.2)*sx; h=cmd.get("h",.15)*sy
            getattr(draw, kind)((x,y,x+w,y+h),outline=INK,width=4)
        if cmd.get("label") and progress>.75:
            x,y = pt(cmd.get("x",cmd.get("x2",.5)),cmd.get("y",cmd.get("y2",.5)))
            draw.text((x+12,y-28),cmd["label"],fill=MUTED,font=_font(18))
        if kind=="text" and progress>.2:
            x,y=pt(cmd.get("x",.2),cmd.get("y",.8)); draw.text((x,y),cmd.get("text",""),fill=INK,font=_font(20))


def export_mp4(lesson: dict, lesson_dir: Path, output: Path, progress: Callable[[float],None] | None=None,
               duration_limit: float | None=None) -> None:
    duration = min(float(lesson["duration"]), duration_limit) if duration_limit else float(lesson["duration"])
    frames = max(1, math.ceil(duration*RENDER_FPS)); renderer = FrameRenderer(lesson, lesson_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".tmp.mp4")
    audio_path = lesson_dir / lesson["audio_file"]
    audio_codec = ["-c:a", "copy"] if audio_path.suffix.lower() in {".m4a", ".mp4", ".aac"} else ["-c:a", "aac", "-b:a", "192k"]
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
               "-f","rawvideo","-pix_fmt","rgb24","-s",f"{WIDTH}x{HEIGHT}","-r",str(RENDER_FPS),"-i","-",
               "-i",str(audio_path),"-c:v","libx264","-preset","medium","-crf","28",
               "-pix_fmt","yuv420p","-r",str(FPS),*audio_codec,"-t",f"{duration:.3f}",
               "-movflags","+faststart",str(temp)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        assert process.stdin
        for index in range(frames):
            process.stdin.write(renderer.frame(index/RENDER_FPS).tobytes())
            if progress and index % (RENDER_FPS*2) == 0: progress(index/frames)
        process.stdin.close(); error = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
        code = process.wait()
        if code: raise RuntimeError(error[-1000:] or f"FFmpeg exited with code {code}")
        temp.replace(output)
        if progress: progress(1)
    except Exception:
        process.kill(); temp.unlink(missing_ok=True); raise
