"""Local document structure and conservative, lecture-wide audio grounding."""
from __future__ import annotations

import math
import os
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

COMMON = set("a an and are as at be by can do for from has have in into is it of on or that the their then there these this to was were what when where which with you your we our they them about like just very really now okay right so next first second third".split())
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13}
HEADING = re.compile(r"^(?:\d+(?:\.\d+)*\s+[A-Za-z]{3,}|(?:Exercises?|Problems?)\s+for\b)", re.I)
NAMED = re.compile(r"^(Definition|Theorem|Example|Remark|Exercise|Proof|Corollary|Proposition)\s*([\d.]+)?", re.I)
EXERCISE = re.compile(r"^(\d{1,2})\.\s+[A-Za-z]", re.I)
MATH = re.compile(r"[=∑∂∇∫±×÷≤≥≈αβηλΣ∆√]|\(cid:\d+\)")
BULLET_CHARS = "•–▪◦◼▪▫▸▹\uf06c\uf0a7\uf0d8\uf0fc"
KEEP_PLURAL = {"stokes", "series", "species", "means", "values", "coordinates", "bayes", "physics", "mathematics", "statistics", "logistics", "sciences", "news", "process", "analysis", "basis", "axis", "status", "corpus", "canvas", "focus", "radius"}


def _norm_token(word: str) -> str:
    if len(word) < 5 or word in KEEP_PLURAL or not word.isalpha():
        return word
    if word.endswith("ies") and len(word) >= 6:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith(("ss", "us", "is", "as", "os", "ics", "es")):
        return word[:-1]
    if word.endswith(("tes", "pes", "les", "res", "nes", "des", "ges", "ves", "ces", "kes")) and len(word) >= 6:
        return word[:-1]
    return word


def tokens(value: str) -> list[str]:
    value = value.lower().replace("’", "'").replace("stoke's", "stokes")
    value = value.replace("divergence", "divergence").replace("decent", "descent")
    value = value.replace("rms prop", "rmsprop").replace("atom optimizer", "adam optimizer")
    value = value.replace("min kovski", "minkowski").replace("a similarity matrix", "dissimilarity matrix")
    return [_norm_token(t) for t in re.findall(r"[a-z]+|\d+(?:\.\d+)*", value) if t not in COMMON]


def _region(items: list[dict]) -> list[float]:
    x = min(w["x"] for w in items); y = min(w["y"] for w in items)
    right = max(w["x"] + w["w"] for w in items)
    bottom = max(w["y"] + w["h"] for w in items)
    return [round(x, 5), round(y, 5), round(right-x, 5), round(bottom-y, 5)]


@lru_cache(maxsize=1)
def _ocr_engine():
    from rapidocr import RapidOCR
    return RapidOCR()


def ocr_words(image_path: Path) -> list[dict]:
    """OCR only pages without usable embedded text; preserve image coordinates."""
    from PIL import Image

    with Image.open(image_path) as im:
        width, height = im.size
    result = _ocr_engine()(str(image_path))
    words = []
    for box, text, score in zip(result.boxes if result.boxes is not None else [],
                                result.txts or [], result.scores if result.scores is not None else []):
        if not text or float(score) < .45:
            continue
        left = min(float(p[0]) for p in box); right = max(float(p[0]) for p in box)
        top = min(float(p[1]) for p in box); bottom = max(float(p[1]) for p in box)
        parts = text.split()
        total = sum(len(part) for part in parts) + max(0, len(parts)-1)
        cursor = left
        for part in parts:
            span = (right-left) * len(part) / max(1,total)
            words.append({"text":part, "x":round(cursor/width,5), "y":round(top/height,5),
                          "w":round(span/width,5), "h":round((bottom-top)/height,5),
                          "ocr_confidence":round(float(score),3)})
            cursor += (right-left)*(len(part)+1)/max(1,total)
    return words


def _lines(page: dict) -> list[dict]:
    rows: list[dict] = []
    for word in sorted(page.get("words", []), key=lambda w: (w["y"], w["x"])):
        is_bullet = word["text"].strip() in BULLET_CHARS
        row = next((r for r in reversed(rows[-6:])
                    if abs(r["y"]-word["y"]) < .009 or
                    (is_bullet and r["y"]-.005 <= word["y"] <= r["y"]+.024)), None)
        if row is None:
            row = {"y":word["y"], "words":[]}
            rows.append(row)
        row["words"].append(word)
    rows = [r for r in rows if any(w["text"].strip() not in BULLET_CHARS for w in r["words"])]
    for row in rows:
        row["words"].sort(key=lambda w:w["x"])
        row["text"] = " ".join(w["text"] for w in row["words"])
        non_bullet = [w for w in row["words"] if w["text"].strip() not in BULLET_CHARS]
        row["region"] = _region(non_bullet or row["words"])
    return sorted(rows, key=lambda r:(r["y"],r["region"][0]))


def _kind(text: str) -> str:
    if EXERCISE.match(text): return "exercise"
    if HEADING.match(text): return "heading"
    if NAMED.match(text):
        name = NAMED.match(text).group(1).lower()
        return "paragraph" if name == "proof" else name
    alpha = len(re.findall(r"[A-Za-z]", text))
    if MATH.search(text) and ("=" in text or "(cid:" in text) and alpha < max(15, len(text)*.5):
        return "equation"
    return "paragraph"


def _boilerplate_key(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text.lower())).strip()


def build_document_map(pages: list[dict]) -> None:
    """Attach positioned blocks and meaningful titles, excluding repeated margins."""
    line_pages = [_lines(page) for page in pages]
    frequency = Counter()
    for lines in line_pages:
        frequency.update({_boilerplate_key(row["text"]) for row in lines
                          if row["y"] < .14 or row["y"] > .9})
    bullet_prefix_re = re.compile(rf"^[{re.escape(BULLET_CHARS)}]\s*")
    for page, lines in zip(pages, line_pages):
        is_slide = page.get("width", 0) > page.get("height", 0)
        content = []
        for row in lines:
            text = row["text"].strip()
            key = _boilerplate_key(text)
            explicit_margin = bool(re.search(r"\bpage\s+\d+\s+of\b|\blecturer:|\bsemester\b|\bintake\b", text, re.I))
            slide_heading = is_slide and row["y"] < .16 and row["region"][3] >= .042 and not explicit_margin
            if not text or ((row["y"] < .14 or row["y"] > .9) and not slide_heading and
                            (frequency[key] >= 3 or explicit_margin)):
                continue
            content.append(row)
        blocks = []
        current = []
        kind = "paragraph"
        def flush():
            if not current: return
            joined = " ".join(line["text"] for line in current)
            words = [w for line in current for w in line["words"] if w["text"].strip() not in BULLET_CHARS]
            if not words:
                words = [w for line in current for w in line["words"]]
            blocks.append({"id":f"pdf-{page['number']}-b{len(blocks)+1}", "page":page["number"],
                           "kind":kind, "text":joined, "region":_region(words),
                           "line_regions":[line["region"] for line in current],
                           "source_quality":page.get("text_quality","embedded")})
            current.clear()
        for row in content:
            line_kind = _kind(row["text"])
            gap = row["y"]-(current[-1]["region"][1]+current[-1]["region"][3]) if current else 0
            starts_bullet = bool(bullet_prefix_re.match(row["text"].strip()))
            if (line_kind == "paragraph" and is_slide and row["y"] < .16 and not starts_bullet and
                    len(re.findall(r"[A-Za-z]{3,}",row["text"].split("•")[0])) >= 1 and
                    "=" not in row["text"].split("•")[0]):
                line_kind = "heading"
            elif (line_kind == "paragraph" and is_slide and not current and not blocks and
                    row["region"][3] >= .065 and len(row["text"].split()) <= 8 and not starts_bullet):
                line_kind = "heading"
            words_in_line = re.findall(r"[A-Za-z]{3,}",row["text"])
            if (line_kind == "paragraph" and (not is_slide or row["y"] < .18) and not starts_bullet and
                    gap > .016 and 3 <= len(words_in_line) <= 9 and
                    not re.search(rf"[.:,;={re.escape(BULLET_CHARS)}]",row["text"]) and
                    sum(word[:1].isupper() for word in words_in_line)/len(words_in_line) >= .6):
                line_kind = "heading"
            title_wrap = (kind=="heading" and line_kind in {"paragraph","heading"} and gap<.035 and
                          len(row["text"].split())<=5 and not starts_bullet and
                          (re.search(r"\b(for|to|of|in|with|and|or|vs\.?|on|learning|parameters|neural)\s*$",current[-1]["text"],re.I) or
                           abs(row["region"][3]-current[-1]["region"][3])<.02))
            max_para_gap = .026 if (is_slide and not starts_bullet) else .016
            if current and not title_wrap and (line_kind != "paragraph" or kind in {"heading","equation"} or starts_bullet or gap > max_para_gap):
                flush()
            if not current: kind = "heading" if title_wrap else line_kind
            current.append(row)
            # A displayed formula should not swallow the following prose.
            if line_kind == "equation" and kind == "equation": flush()
        flush()
        if (len(blocks)>=2 and blocks[0]["kind"]=="heading" and blocks[1]["kind"] in {"heading","paragraph"} and
                blocks[1]["region"][1]<.22 and len(blocks[1]["text"].split())<=6 and
                not bullet_prefix_re.match(blocks[1]["text"].strip()) and
                (re.search(r"\b(for|to|of|in|with|and|or|vs\.?|learning|parameters|neural)\s*$",blocks[0]["text"],re.I) or
                 re.match(r"^(learning\s+parameters|parameters\s+in|fine\s+granularity|networks?)\b",blocks[1]["text"],re.I))):
            first,continuation=blocks[0],blocks.pop(1)
            first["text"] += " " + continuation["text"]
            x=min(first["region"][0],continuation["region"][0]);y=min(first["region"][1],continuation["region"][1])
            right=max(first["region"][0]+first["region"][2],continuation["region"][0]+continuation["region"][2])
            bottom=max(first["region"][1]+first["region"][3],continuation["region"][1]+continuation["region"][3])
            first["region"]=[x,y,right-x,bottom-y]
            first["line_regions"] += continuation["line_regions"]
            for position,block in enumerate(blocks,1): block["id"]=f"pdf-{page['number']}-b{position}"
        for visual in page.get("visuals",[]):
            if visual.get("kind") == "figure":
                blocks.append({"id":f"pdf-{page['number']}-b{len(blocks)+1}","page":page["number"],
                               "kind":"figure","text":visual.get("label","figure diagram graph"),
                               "region":visual["region"],"line_regions":[],
                               "source_quality":page.get("text_quality","embedded")})
        if not blocks:
            blocks.append({"id":f"pdf-{page['number']}-b1","page":page["number"],
                           "kind":"unreadable","text":"","region":[0.,0.,1.,1.],
                           "line_regions":[],"source_quality":"unreadable"})
        section_id=None; named_id=None
        for block in blocks:
            if block["kind"]=="heading":
                section_id=block["id"];named_id=None
            elif block["kind"] in {"definition","theorem","example","exercise","remark"}:
                named_id=block["id"]
            block["section_id"]=section_id
            block["parent_id"]=named_id if block["id"]!=named_id else section_id
        heading = next((b["text"] for b in blocks if b["kind"] == "heading"), None)
        page["title"] = (heading or next((b["text"] for b in blocks), f"Page {page['number']}"))[:100]
        page["blocks"] = blocks
        page["text"] = "\n".join(b["text"] for b in blocks)
        page["search_text"] = page["text"]
    page_tok_sets = [set(tokens(p.get("text", ""))) for p in pages]
    for idx, page in enumerate(pages):
        title_lower = page.get("title", "").lower()
        explicit_outline = bool(re.search(r"\b(outline|agenda|table of contents|syllabus)\b", title_lower))
        toks = page_tok_sets[idx]
        repeated_divider = bool(toks and any(
            abs(idx - j) >= 2 and page_tok_sets[j] and
            len(toks & page_tok_sets[j]) / len(toks | page_tok_sets[j]) > .75 and
            any(pages[k].get("title") != page.get("title") for k in range(min(idx, j)+1, max(idx, j)))
            for j in range(len(pages))
        ))
        page["is_outline"] = explicit_outline or repeated_divider


def _idf(blocks: list[dict]) -> dict[str,float]:
    freq = Counter(t for b in blocks for t in set(tokens(b["text"])))
    return {t:math.log(1+(len(blocks)-count+.5)/(count+.5)) for t,count in freq.items()}


def _lexical(query: str, block: dict, idf: dict[str,float]) -> float:
    q_list = tokens(query)
    q = set(q_list); b = Counter(tokens(block["text"]))
    if not q or not b: return 0.
    if len(q_list) >= 10:
        split_idx = max(1, int(len(q_list) * .35))
        early = set(q_list[:split_idx])
        late = set(q_list[split_idx:])
        def _w(tok: str) -> float:
            base = idf.get(tok, 1.)
            return base * (1.18 if tok in late and tok not in early else (.88 if tok in early and tok not in late else 1.))
    else:
        def _w(tok: str) -> float:
            return idf.get(tok, 1.)
    overlap = sum(_w(t)*min(1,b[t]) for t in q)
    maximum = sum(_w(t) for t in q)
    precision = sum(idf.get(t,1)*min(1,b[t]) for t in q) / max(1.,sum(idf.get(t,1) for t in b))
    return min(1., .77*overlap/max(1.,maximum)+.23*precision)


def _section_number(text: str) -> str | None:
    match = re.search(r"\b(\d{1,2})\s*(?:\.|\bpoint\b)\s*(\d{1,2})\b",text,re.I)
    if match: return f"{match.group(1)}.{match.group(2)}"
    for left,ln in NUMBER_WORDS.items():
        for right,rn in NUMBER_WORDS.items():
            if re.search(rf"\b{left}\s+point\s+{right}\b",text,re.I): return f"{ln}.{rn}"
    return None


CUE_RE = re.compile(
    r"\b(?:subtopic|main topic|next topic|section)"
    r"(?:\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen))?"
    r"(?:\s+(?:here\s+is|is|under\s+that\s+is))?\s*[,:;.!?-]*",
    re.I,
)
DANGLING_CUE_RE = re.compile(
    r"\b(?:subtopic|main topic|next topic|section)"
    r"(?:\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen))?\s*[?.!,:]*$",
    re.I,
)


def _anchor(
    text: str,
    blocks: list[dict],
    min_page: int = 1,
    outline_pages: set[int] | None = None,
    prev_cue: bool = False,
) -> str | None:
    lower = text.lower()
    number = _section_number(lower)
    if number and re.search(r"\b(subtopic|section|topic|chapter)\b",lower):
        for b in blocks:
            if b["kind"] == "heading" and re.match(rf"^{re.escape(number)}\b",b["text"]): return b["id"]
    if "exercises for lecture" in lower:
        return next((b["id"] for b in blocks if b["kind"] == "heading" and
                     "exercises for lecture" in b["text"].lower()),None)
    named = re.search(r"\b(example|definition|theorem)\s+(\d+(?:\.\d+)*)\b",lower)
    if named:
        kind,number=named.groups()
        found=next((b["id"] for b in blocks if b["kind"]==kind and
                    re.match(rf"^{kind}\s+{re.escape(number)}\b",b["text"],re.I)),None)
        if found: return found
    for kind in ("example","definition"):
        match=re.search(rf"\b{kind}\s+(one|two|three|four|five|six|seven|eight)\b",lower)
        if match:
            digit=NUMBER_WORDS[match.group(1)]
            found=next((b["id"] for b in blocks if b["kind"]==kind and
                        re.match(rf"^{kind}\s+{digit}\b",b["text"],re.I)),None)
            if found: return found
    match = re.search(r"\bexercise\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",lower)
    if match:
        value = match.group(1); digit = int(value) if value.isdigit() else NUMBER_WORDS[value]
        return next((b["id"] for b in blocks if b["kind"] == "exercise" and
                     re.match(rf"^{digit}\.\s",b["text"])),None)
    cue_tails: list[str] = []
    if prev_cue:
        cleaned_start = re.sub(r"^(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen)\s*[,:;.!?-]*\s*", "", lower.lstrip())
        if cleaned_start:
            cue_tails.append(cleaned_start)
    for m in CUE_RE.finditer(lower):
        tail = lower[m.end():].lstrip(" ,:.!?")
        if tail:
            cue_tails.append(tail)
    if cue_tails:
        explicit_back = bool(re.search(r"\b(back to|return to|earlier|previous|recall)\b", lower))
        headings = [b for b in blocks if b["kind"] == "heading"]
        heading_freq = Counter(t for b in headings for t in set(tokens(b["text"].split("•")[0])))
        valid_matches = []
        for cue_idx, tail in enumerate(cue_tails):
            tail_toks = tokens(tail)
            if not tail_toks:
                continue
            window = tail_toks[:8]
            short_window = set(tail_toks[:4])
            spoken = set(window)
            for b in headings:
                raw_head = b["text"].split("•")[0]
                sec_prefix = re.match(r"^(\d+\.\d+)\b", raw_head.strip())
                title_variants = [set(tokens(raw_head))]
                if ":" in raw_head:
                    sub = {t for t in tokens(raw_head.split(":", 1)[1]) if not t.isdigit()}
                    if len(sub) >= 2 or (len(sub) == 1 and all(heading_freq[t] == 1 for t in sub)):
                        title_variants.append(sub)
                for title in title_variants:
                    if not title:
                        continue
                    if sec_prefix and sec_prefix.group(1) in title and sec_prefix.group(1) not in spoken:
                        continue
                    overlap = title & spoken
                    if not overlap:
                        continue
                    weight = sum(1 / math.sqrt(max(1, heading_freq[t])) for t in overlap)
                    precision = len(overlap) / len(title)
                    exact_short = (
                        1 <= len(title) <= 2
                        and title.issubset(short_window)
                        and (len(title) == 2 or heading_freq[next(iter(title))] <= 2 or (b["page"] == min_page + 1 and tail_toks[0] in title))
                    )
                    long_match = (
                        len(overlap) >= 3
                        and precision >= .68
                        and len(overlap) / max(1, min(len(title), len(spoken))) >= .70
                    )
                    if exact_short or long_match:
                        is_outline = bool(outline_pages and b["page"] in outline_pages and b["page"] > 3)
                        first_pos = min((window.index(t) for t in overlap if t in window), default=99)
                        valid_matches.append({
                            "block": b,
                            "page": b["page"],
                            "title": title,
                            "overlap": len(overlap),
                            "precision": precision,
                            "weight": weight,
                            "is_outline": is_outline,
                            "first_pos": first_pos,
                            "cue_idx": cue_idx,
                            "has_and": "and" in tail.split()[:6],
                        })
        if valid_matches:
            non_outline = [m for m in valid_matches if not m["is_outline"]]
            pool = non_outline if non_outline else valid_matches
            if not explicit_back:
                forward_pool = [m for m in pool if m["page"] >= min_page]
                if forward_pool:
                    pool = forward_pool
                else:
                    pool = [
                        m for m in pool
                        if m["overlap"] >= 3 and m["precision"] >= .75
                        and not (outline_pages and m["page"] in outline_pages)
                    ]
            if pool:
                next_step_pages = {min_page + 1}
                if outline_pages and (min_page + 1) in outline_pages:
                    next_step_pages.add(min_page + 2)
                max_overlap = max(m["overlap"] for m in pool)
                immediate = [
                    m for m in pool
                    if m["has_and"] and m["page"] in next_step_pages and m["precision"] >= .99
                    and m["first_pos"] == 0 and (m["overlap"] >= max_overlap or len(m["title"]) <= 2)
                ]
                if immediate:
                    best = max(immediate, key=lambda m: (m["cue_idx"], m["weight"], -m["page"]))
                    return best["block"]["id"]
                best = max(
                    pool,
                    key=lambda m: (
                        round(m["weight"], 3),
                        m["precision"],
                        m["cue_idx"],
                        -m["first_pos"],
                        -m["page"],
                    ),
                )
                return best["block"]["id"]
    return None


def _embeddings(blocks: list[dict], points: list[dict], model_dir: Path | None):
    if not model_dir or os.getenv("AUDIO_TO_PDF_DISABLE_EMBEDDINGS") == "1": return None
    try:
        from fastembed import TextEmbedding
        import numpy as np
        cached = (model_dir / "models--Qdrant--bge-small-en-v1.5-onnx-Q").exists()
        try:
            model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=str(model_dir), local_files_only=cached)
        except Exception:
            model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=str(model_dir))
        bvec = np.asarray(list(model.embed(["passage: "+b["text"][:1200] for b in blocks])),dtype=float)
        pvec = np.asarray(list(model.embed(["query: "+p["text"][:900] for p in points])),dtype=float)
        bvec /= np.maximum(np.linalg.norm(bvec,axis=1,keepdims=True),1e-9)
        pvec /= np.maximum(np.linalg.norm(pvec,axis=1,keepdims=True),1e-9)
        return pvec @ bvec.T
    except Exception:
        # Local model unavailable: finish with lexical evidence and expose the mode.
        return None


def _word_region(text: str, page: dict, block: dict) -> tuple[list[float] | None,float]:
    x,y,w,h = block["region"]
    words = [v for v in page["words"] if x-.004 <= v["x"] <= x+w+.004 and
             y-.004 <= v["y"] <= y+h+.004 and v["text"].strip() not in BULLET_CHARS]
    q = set(tokens(text))
    candidates = [v for v in words if set(tokens(v["text"])) & q]
    if not candidates: return None,0.
    lines: dict[int,list[dict]] = defaultdict(list)
    for v in candidates: lines[round(v["y"]*100)].append(v)
    selected = max(lines.values(),key=lambda row:(len(set(t for v in row for t in tokens(v["text"]))&q),
                                                  -min(v["y"] for v in row)))
    unique = len(set(t for v in selected for t in tokens(v["text"])) & q)
    block_toks = set(tokens(block["text"]))
    short_complete = 1 <= len(block_toks) <= 2 and unique >= len(block_toks)
    if unique < 2 and not short_complete and block["kind"] not in {"heading","example","exercise"}:
        return None,0.
    box = _region(selected)
    return [box[0],round(min(.99,box[1]+box[3]+.003),5),box[2],.006], min(1.,unique/max(1,min(3,len(block_toks))))


def align_document(points: list[dict], pages: list[dict], model_dir: Path | None = None) -> list[dict]:
    """Align globally by page, then locally by block; abstain on weak evidence."""
    if not points or not pages:
        return []
    blocks = [b for p in pages for b in p.get("blocks",[])]
    if not blocks: return []
    index = {b["id"]:i for i,b in enumerate(blocks)}
    idf = _idf(blocks)
    semantic = _embeddings(blocks,points,model_dir)
    outline_pages = {p["number"] for p in pages if p.get("is_outline")}
    scores = []
    anchors = []
    running_page = 1
    for i,point in enumerate(points):
        prev_cue = i > 0 and not anchors[i-1] and bool(DANGLING_CUE_RE.search(points[i-1]["text"].strip()))
        anchor = _anchor(point["text"], blocks, min_page=running_page, outline_pages=outline_pages, prev_cue=prev_cue)
        anchors.append(anchor)
        if anchor:
            running_page = blocks[index[anchor]]["page"]
        row = []
        for j,b in enumerate(blocks):
            lexical = _lexical(point["text"],b,idf)
            sem = max(0.,float(semantic[i,j])) if semantic is not None else 0.
            combined = .68*lexical + (.32*sem if semantic is not None else 0.)
            if anchor == b["id"]: combined += 2.
            row.append((combined,lexical,sem))
        scores.append(row)
    npage = len(pages)
    duplicate_neighbors = set()
    for p in range(2,npage+1):
        before=set(tokens(pages[p-2].get("text","")))
        after=set(tokens(pages[p-1].get("text","")))
        if before and after and len(before & after)/max(1,len(before | after))>.82:
            duplicate_neighbors.add(p)
    upcoming_top_anchor = [None]*len(points)
    next_top = None
    for i in range(len(points)-1,-1,-1):
        upcoming_top_anchor[i]=next_top
        if anchors[i]:
            b=blocks[index[anchors[i]]]
            if b["kind"]=="heading" and b["region"][1]<.27:
                next_top=b["page"]
    # Viterbi over PDF pages. Strong announced headings/exercise numbers fix their page.
    back = []
    previous = [0.] + [-1e6]*(npage-1)
    for i,point in enumerate(points):
        anchor_page = blocks[index[anchors[i]]]["page"] if anchors[i] else None
        emission = [max((s[0] for s,b in zip(scores[i],blocks) if b["page"]==p),default=0.)
                    for p in range(1,npage+1)]
        current=[]; choices=[]
        for p in range(1,npage+1):
            if (anchor_page and p != anchor_page) or (not anchor_page and upcoming_top_anchor[i]
                    and p > max(1,upcoming_top_anchor[i]-1)):
                current.append(-1e6);choices.append(0);continue
            options=[]
            for q in range(1,npage+1):
                # A generic recap must not send the viewer backwards. Only an
                # explicit grounded section/exercise anchor can do that.
                if p<q and not anchor_page:
                    options.append(-1e6);continue
                penalty = 0 if p==q else .14+.13*max(0,p-q-1) if p>q else .08+.04*(q-p-1)
                if p>q and p in duplicate_neighbors: penalty += .35
                if p>q and p in outline_pages and p>3 and not anchor_page: penalty += .5
                options.append(previous[q-1]-penalty)
            best=max(range(npage),key=lambda q:options[q])
            current.append(options[best]+emission[p-1]);choices.append(best)
        back.append(choices);previous=current
    selected_pages=[1]*len(points)
    state=max(range(npage),key=lambda q:previous[q])
    for i in range(len(points)-1,-1,-1):
        selected_pages[i]=state+1;state=back[i][state]
    # Repair the path forward from explicit anchors. This prevents a later
    # difficult transition from erasing an earlier, directly announced page.
    page_tokens = {p["number"]: set(tokens(p.get("text", ""))) for p in pages}
    last_page=1
    for i in range(len(points)):
        if anchors[i]:
            last_page=blocks[index[anchors[i]]]["page"]
            selected_pages[i]=last_page
            continue
        next_fwd_idx=next((k for k in range(i+1,len(points)) if anchors[k] and blocks[index[anchors[k]]]["page"]>=last_page),None)
        next_anchor=blocks[index[anchors[next_fwd_idx]]]["page"] if next_fwd_idx is not None else None
        continuation = bool(next_anchor and pages[next_anchor-1].get("blocks") and
                            pages[next_anchor-1]["blocks"][0]["kind"] in {"paragraph","equation"})
        upper=npage if next_anchor is None else max(last_page,next_anchor if continuation else next_anchor-1)
        allowed_pages = [p for p in range(last_page,upper+1) if p==last_page or not (p in outline_pages and p>3)]
        page_scores={p:max((s[0] for s,b in zip(scores[i],blocks) if b["page"]==p),default=0.)
                     for p in allowed_pages}
        page_lex={p:max((s[1] for s,b in zip(scores[i],blocks) if b["page"]==p),default=0.)
                  for p in allowed_pages}
        current_score=page_scores.get(last_page,0.)
        jump_cost=.04 if npage<=10 else .14
        def _eff_steps(p: int) -> int:
            if p <= last_page:
                return 0
            skipped_outlines = sum(1 for m in range(last_page + 1, p) if m in outline_pages and m > 3)
            return max(1, (p - last_page) - skipped_outlines)
        candidate=max(page_scores,key=lambda p:page_scores[p]-jump_cost*_eff_steps(p))
        if candidate>last_page and page_scores[candidate]-jump_cost*_eff_steps(candidate) >= current_score+.035:
            spoken_toks = set(tokens(points[i]["text"]))
            novel_toks = spoken_toks & (page_tokens.get(candidate, set()) - page_tokens.get(last_page, set()))
            if page_lex.get(candidate, 0.) >= .20 and novel_toks:
                last_page=candidate
        selected_pages[i]=last_page
    # Second sequence pass: monotonic top-to-bottom progression within each page.
    block_back=[]; prior={}
    for i,(point,page_num) in enumerate(zip(points,selected_pages)):
        available=[j for j,b in enumerate(blocks) if b["page"]==page_num]
        if anchors[i] and index[anchors[i]] in available:
            available=[index[anchors[i]]]
        current={}; origins={}
        explicit_back=bool(re.search(r"\b(previous|earlier|back to|recall|first characteristic)\b",point["text"],re.I))
        for j in available:
            emission=scores[i][j][0]
            lex_j=scores[i][j][1]
            # Cooperative parent/child bullet support: if a short 1-2 word bullet header
            # and its immediate sub-bullet on the same page are spoken together, boost slightly.
            if (blocks[j]["kind"]=="paragraph" and 1<=len(set(tokens(blocks[j]["text"])))<=2
                    and lex_j>=.22 and j+1 in available and scores[i][j+1][1]>=.20):
                emission += .04
            if not prior:
                current[j]=emission;origins[j]=None;continue
            options=[]
            for k,value in prior.items():
                if blocks[k]["page"]!=page_num:
                    # Starting a new page prefers starting near the top of the page.
                    pos_in_page = available.index(j) if j in available else 0
                    penalty = .02 + .015 * min(10, pos_in_page)
                elif j==k:
                    penalty=0.
                elif j>k:
                    step = j - k
                    if step <= 2 and lex_j >= .18:
                        penalty = -.025 if step == 1 else -.01
                    else:
                        penalty = .025 + .014 * min(20, step)
                else:
                    penalty=(.04 if explicit_back else .52)+.04*min(20,k-j)
                options.append((value-penalty,k))
            best_value,origin=max(options,key=lambda row:row[0])
            current[j]=best_value+emission;origins[j]=origin
        block_back.append(origins);prior=current
    selected_blocks=[0]*len(points)
    cursor=max(prior,key=prior.get)
    for i in range(len(points)-1,-1,-1):
        selected_blocks[i]=cursor
        cursor=block_back[i][cursor]
    result=[]
    for i,(point,page_num) in enumerate(zip(points,selected_pages)):
        page=pages[page_num-1]
        candidates=[(scores[i][j],b,j) for j,b in enumerate(blocks) if b["page"]==page_num]
        candidates.sort(key=lambda row:row[0][0],reverse=True)
        sel_idx = selected_blocks[i]
        block=blocks[sel_idx]
        score,lexical,sem=scores[i][sel_idx]
        prev_idx = selected_blocks[i-1] if i > 0 and selected_pages[i-1] == page_num else -1
        # Exclude already-passed upper blocks (j < prev_idx) and cooperative parent/sub-bullet
        # neighbors when computing the competing runner-up score on the same page.
        competing_scores = [
            s[0] for s, b, j in candidates
            if b["id"] != block["id"]
            and j >= max(0, prev_idx)
            and not (abs(j - sel_idx) == 1 and min(len(set(tokens(block["text"]))), len(set(tokens(b["text"])))) <= 2)
        ]
        next_score=max(competing_scores,default=0.)
        anchored=anchors[i]==block["id"]
        # Semantic similarity alone is insufficient to draw a mark.
        supported=anchored or (lexical>=.16 and score>=.18 and score-next_score>=.025)
        if block["kind"] == "unreadable": supported=False
        kind = block["kind"]
        region=None;action="hold"
        if supported and kind == "equation":
            # Garbled symbols must be reviewed, never given an automatic box.
            if "(cid:" not in block["text"] and lexical>=.24:
                action="box";region=block["region"]
        elif supported and kind == "figure":
            action="box";region=block["region"]
        elif supported:
            region,word_strength=_word_region(point["text"],page,block)
            if region and (anchored or lexical>=.22): action="underline"
        quality=block.get("source_quality","embedded")
        if quality=="ocr" and action=="underline" and lexical<.34: action="hold";region=None
        confidence=round(min(.98, (.84 if anchored else .32+score*.75+max(0.,score-next_score)*.5)),3)
        reviewed=action=="hold" or (not anchored and confidence<.55)
        if reviewed: action="hold";region=None
        has_eq = any(v.get("kind") == "equation" for v in page.get("visuals", [])) or any(b["kind"] == "equation" for b in page.get("blocks", []))
        spoken_eq = has_eq and bool(re.search(r"\b(formula|equation|partial derivative|new weight|old weight|learning rate|alpha|beta|epsilon|sigma|sum of)\b", point["text"], re.I))
        ref_kind = "equation" if (kind == "equation" or spoken_eq) else "source" if action != "hold" else "none"
        result.append({"id":point.get("id",f"p{i+1:04d}"),"start":point["start"],"end":point["end"],
                       "text":point["text"],"words":point.get("words",[]),
                       "topic":page["title"],"concept":" ".join(point["text"].split()[:12]),
                       "reference_kind":ref_kind,
                       "page":page_num,"page_id":f"pdf-{page_num}","block_id":block["id"] if supported else None,
                       "parent_block_id":block.get("parent_id") if supported else None,
                       "action":action,"region":region,"sketch":None,"confidence":confidence,
                       "needs_review":reviewed,"evidence":{"block_score":round(score,3),
                       "lexical":round(lexical,3),"semantic":round(sem,3),
                       "margin":round(score-next_score,3),"anchor":bool(anchored),
                       "matcher":"local-semantic" if semantic is not None else "local-lexical"}})
    return result
