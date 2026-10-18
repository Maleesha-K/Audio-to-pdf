"""Extract conservative equation and graph targets from the original PDF."""

from __future__ import annotations

import re


def _region(box, width, height):
    x0, top, x1, bottom = box
    pad_x, pad_y = 5, 5
    return [round(max(0, x0-pad_x)/width, 4), round(max(0, top-pad_y)/height, 4),
            round((min(width, x1+pad_x)-max(0, x0-pad_x))/width, 4),
            round((min(height, bottom+pad_y)-max(0, top-pad_y))/height, 4)]


def extract_visuals(page):
    """Return visible regions; never infer an equation box from nearby prose."""
    width, height = float(page.width), float(page.height)
    visuals = []
    chars = page.chars
    # Printed formulas have mathematical glyphs or operators. Group nearby
    # glyphs into a line, including surrounding operands and indices.
    math_chars = [c for c in chars if c.get("text", "") in "=∑∂∇∫±×÷≤≥≈αβηλΣ∆√" or
                  "symbol" in c.get("fontname", "").lower()]
    math_rows = {}
    for char in math_chars:
        key = round(float(char["top"]) / 13)
        math_rows.setdefault(key, []).append(char)
    for group in math_rows.values():
        seed_top = min(c["top"] for c in group)
        seed_bottom = max(c["bottom"] for c in group)
        seed_left = min(c["x0"] for c in group)
        seed_right = max(c["x1"] for c in group)
        near = [c for c in chars if c["bottom"] >= seed_top-8 and c["top"] <= seed_bottom+8]
        if not near:
            continue
        x0, top = min(c["x0"] for c in near), min(c["top"] for c in near)
        x1, bottom = max(c["x1"] for c in near), max(c["bottom"] for c in near)
        label = "".join(c.get("text", "") for c in sorted(near, key=lambda c: (round(c["top"]/8), c["x0"])))
        # Short body-text equations such as h(Σwx) are still useful targets.
        prose_words = re.findall(r"[A-Za-z]{3,}", label)
        if x1-x0 >= 15 and bottom-top >= 8 and x1-x0 < width*.75 and len(prose_words) <= 3 and "•" not in label:
            visuals.append({"kind": "equation", "region": _region((x0, top, x1, bottom), width, height),
                            "label": label[:250]})
    # PDF image objects are strong evidence for graphs/diagrams. Discard
    # tiny logos and speaker icons, then merge adjacent image fragments.
    candidates = [i for i in page.images if (i["x1"]-i["x0"]) > 65 and
                  (i["bottom"]-i["top"]) > 45 and i["top"] < height*.82]
    if candidates:
        x0 = min(i["x0"] for i in candidates); x1 = max(i["x1"] for i in candidates)
        top = min(i["top"] for i in candidates); bottom = max(i["bottom"] for i in candidates)
        # Multiple unrelated figures on one page remain reviewable.
        visuals.append({"kind": "figure", "region": _region((x0, top, x1, bottom), width, height),
                        "label": "embedded graph or diagram"})
    # Many PDFs draw graphs as vector paths rather than image objects.
    shapes = []
    for item in list(page.lines) + list(page.curves):
        box = (float(item.get("x0",0)),float(item.get("top",0)),
               float(item.get("x1",0)),float(item.get("bottom",0)))
        sw,sh = box[2]-box[0],box[3]-box[1]
        if (max(sw,sh)>14 and sw<width*.85 and sh<height*.7 and
                box[1]>height*.08 and box[3]<height*.92):
            shapes.append(box)
    groups = []
    for box in shapes:
        touching = [g for g in groups if not (box[2]+12<g[0] or box[0]-12>g[2] or
                                               box[3]+12<g[1] or box[1]-12>g[3])]
        if touching:
            merged = (min([box[0]]+[g[0] for g in touching]),
                      min([box[1]]+[g[1] for g in touching]),
                      max([box[2]]+[g[2] for g in touching]),
                      max([box[3]]+[g[3] for g in touching]),
                      1+sum(g[4] for g in touching))
            groups = [g for g in groups if g not in touching]
            groups.append(merged)
        else:
            groups.append((*box,1))
    for x0,top,x1,bottom,count in groups:
        if count>=3 and x1-x0>60 and bottom-top>45 and not any(
                v["kind"]=="figure" and abs(v["region"][1]-top/height)<.08 for v in visuals):
            nearby = [w["text"] for w in page.extract_words() if top-35<=w["top"]<=bottom+25 and
                      x0-20<=w["x0"]<=x1+20]
            visuals.append({"kind":"figure","region":_region((x0,top,x1,bottom),width,height),
                            "label":" ".join(nearby[:24]) or "vector graph or diagram"})
    # Deduplicate overlapping formula lines.
    unique = []
    for visual in visuals:
        if visual["kind"] == "equation" and any(v["kind"] == "equation" and
                abs(v["region"][1]-visual["region"][1]) < .018 for v in unique):
            continue
        unique.append(visual)
    return unique


def choose_visual(text, page, kind):
    visuals = [v for v in page.get("visuals", []) if v["kind"] == kind]
    if not visuals:
        return None
    if len(visuals) == 1:
        return visuals[0]
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    ranked = sorted(visuals, key=lambda v: len(words & set(re.findall(r"[a-z0-9]+", v["label"].lower()))), reverse=True)
    return ranked[0] if len(ranked) == 1 or ranked[0]["label"] != ranked[1]["label"] else None
