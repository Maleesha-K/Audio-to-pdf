"""Extract conservative equation and graph targets from the original PDF."""

from __future__ import annotations

import re


def _region(box, width, height):
    x0, top, x1, bottom = box
    pad_x, pad_y = 5, 5
    return [round(max(0, x0-pad_x)/width, 4), round(max(0, top-pad_y)/height, 4),
            round((min(width, x1+pad_x)-max(0, x0-pad_x))/width, 4),
            round((min(height, bottom+pad_y)-max(0, top-pad_y))/height, 4)]


MATH_CHARS = set("=∑∂∇∫∬∮±×÷≤≥≠≈∈∞αβγδεζηθικλμνξπρςστυφχψωΔΘΛΞΠΣΦΨΩ∆√−")
PUA_BULLETS = {"\u2022", "\u25fc", "\u25aa", "\u25cf", "\uf0b7", "\uf0a7", "\uf0d8", "\uf0fc"}


def _is_math_char(char: dict) -> bool:
    txt = char.get("text", "")
    if not txt or txt in PUA_BULLETS:
        return False
    cp = ord(txt[0])
    if txt in MATH_CHARS:
        return True
    if 0x1D400 <= cp <= 0x1D7FF:
        return True
    if 0xF000 <= cp <= 0xF0FF and txt not in PUA_BULLETS:
        return True
    font = char.get("fontname", "").lower()
    if "symbol" in font or "cmmi" in font or "cmsy" in font or "cmex" in font:
        return True
    return False


def _clean_formula_Side(cluster: list[dict]) -> list[dict]:
    """Strip leading bullet labels ('Empirical formula:', 'IQR:') from a math cluster."""
    ordered = sorted(cluster, key=lambda c: c["x0"])
    colon_x = None
    for c in ordered:
        if c.get("text") in PUA_BULLETS and c["x0"] < ordered[-1]["x0"] - 20:
            colon_x = max(colon_x or 0.0, float(c["x1"]))
        elif c.get("text") == ":":
            # Only strip before ':' if there is a math operator/symbol to the right of ':'
            right_chars = [r for r in ordered if r["x0"] > c["x1"] + 1]
            if any(_is_math_char(r) for r in right_chars):
                colon_x = max(colon_x or 0.0, float(c["x1"]))
    if colon_x is not None:
        trimmed = [c for c in ordered if c["x0"] > colon_x + 1]
        if len(trimmed) >= 3:
            return trimmed
    return ordered


def extract_visuals(page):
    """Return visible regions; never infer an equation box from nearby prose."""
    width, height = float(page.width), float(page.height)
    visuals = []
    chars = page.chars
    # Pass 1: Original line-level equation extraction (preserves exact legacy boxes).
    math_chars = [c for c in chars if c.get("text", "") in "=∑∂∇∫±×÷≤≥≈αβηλΣ∆√" or
                  "symbol" in c.get("fontname", "").lower()]
    math_rows = {}
    for char in math_chars:
        key = round(float(char["top"]) / 13)
        math_rows.setdefault(key, []).append(char)
    for group in math_rows.values():
        seed_top = min(c["top"] for c in group)
        seed_bottom = max(c["bottom"] for c in group)
        near = [c for c in chars if c["bottom"] >= seed_top-8 and c["top"] <= seed_bottom+8]
        if not near:
            continue
        x0, top = min(c["x0"] for c in near), min(c["top"] for c in near)
        x1, bottom = max(c["x1"] for c in near), max(c["bottom"] for c in near)
        label = "".join(c.get("text", "") for c in sorted(near, key=lambda c: (round(c["top"]/8), c["x0"])))
        prose_words = re.findall(r"[A-Za-z]{3,}", label)
        if x1-x0 >= 15 and bottom-top >= 8 and x1-x0 < width*.75 and len(prose_words) <= 3 and "•" not in label:
            visuals.append({"kind": "equation", "region": _region((x0, top, x1, bottom), width, height),
                            "label": label[:250]})

    # Pass 2: 2D spatial clustering for multi-column, side-by-side, and right-aligned formulas
    # (e.g., Sample vs. Population Mean/Variance, Median interpolation, Empirical formula, IQR, Z-score).
    math_words = {"mean", "mode", "median", "freq", "width", "curl", "div", "vol", "norm", "cos", "sin", "tan", "max", "min", "log", "exp", "iqr"}
    non_bullet = [c for c in chars if c.get("text", "").strip() and c.get("text") not in PUA_BULLETS]
    h_segs: list[list[dict]] = []
    for c in sorted(non_bullet, key=lambda item: (round(item["top"] / 6), item["x0"])):
        cx0, cx1, ctop, cbot = float(c["x0"]), float(c["x1"]), float(c["top"]), float(c["bottom"])
        matched = []
        for idx, seg in enumerate(h_segs):
            for m in seg:
                v_ov = min(cbot, float(m["bottom"])) - max(ctop, float(m["top"]))
                h_d = max(0.0, max(cx0, float(m["x0"])) - min(cx1, float(m["x1"])))
                if v_ov > 1.5 and h_d <= 14:
                    matched.append(idx)
                    break
        if not matched:
            h_segs.append([c])
        else:
            first = matched[0]
            h_segs[first].append(c)
            for other in reversed(matched[1:]):
                h_segs[first].extend(h_segs.pop(other))

    split_segs: list[list[dict]] = []
    for seg in h_segs:
        ordered = sorted(seg, key=lambda item: item["x0"])
        colons = [c for c in ordered if c.get("text") == ":"]
        if colons:
            last_colon_x = max(float(c["x1"]) for c in colons)
            right = [c for c in ordered if float(c["x0"]) > last_colon_x + 1]
            if right:
                split_segs.append(right)
        else:
            split_segs.append(ordered)

    def _is_prose_seg(seg: list[dict]) -> bool:
        txt = "".join(c.get("text", "") for c in sorted(seg, key=lambda item: item["x0"]))
        pwords = [w for w in re.findall(r"[A-Za-z]{4,}", txt) if w.lower() not in math_words]
        if len(pwords) >= 2:
            return True
        if len(pwords) == 1 and not any(_is_math_char(c) for c in seg):
            return True
        return False

    cand_segs = [seg for seg in split_segs if not _is_prose_seg(seg)]

    def _main_eq_ys(cl: list[dict]) -> list[float]:
        ordered = sorted(cl, key=lambda item: item["x0"])
        ys = []
        for idx_c, c in enumerate(ordered):
            if c.get("text") == "=" and (float(c["bottom"]) - float(c["top"])) >= 13.0:
                prev_txt = ordered[idx_c - 1].get("text", "") if idx_c > 0 else ""
                next_txt = ordered[idx_c + 1].get("text", "") if idx_c + 1 < len(ordered) else ""
                if prev_txt in {"i", "f", "k", "j"} and next_txt in {"0", "1"}:
                    continue
                ys.append(0.5 * (float(c["top"]) + float(c["bottom"])))
        return ys

    clusters: list[list[dict]] = []
    for seg in cand_segs:
        sx0, sx1 = min(float(c["x0"]) for c in seg), max(float(c["x1"]) for c in seg)
        stop, sbot = min(float(c["top"]) for c in seg), max(float(c["bottom"]) for c in seg)
        s_eqs = _main_eq_ys(seg)
        matched = []
        for idx, cl in enumerate(clusters):
            cx0, cx1 = min(float(c["x0"]) for c in cl), max(float(c["x1"]) for c in cl)
            ctop, cbot = min(float(c["top"]) for c in cl), max(float(c["bottom"]) for c in cl)
            v_ov = min(sbot, cbot) - max(stop, ctop)
            v_d = max(0.0, max(stop, ctop) - min(sbot, cbot))
            h_ov = min(sx1, cx1) - max(sx0, cx0)
            h_d = max(0.0, max(sx0, cx0) - min(sx1, cx1))
            c_eqs = _main_eq_ys(cl)
            if s_eqs and c_eqs and all(abs(sy - cy) > 18 for sy in s_eqs for cy in c_eqs):
                continue
            if (v_ov > -4 and h_d <= 20) or (h_ov > -4 and v_d <= 12):
                matched.append(idx)
        if not matched:
            clusters.append(list(seg))
        else:
            first = matched[0]
            clusters[first].extend(seg)
            for other in reversed(matched[1:]):
                clusters[first].extend(clusters.pop(other))

    for cl in clusters:
        if not any(_is_math_char(c) for c in cl):
            continue
        cl = _clean_formula_Side(cl)
        if not any(_is_math_char(c) for c in cl):
            continue
        x0, top = min(float(c["x0"]) for c in cl), min(float(c["top"]) for c in cl)
        x1, bottom = max(float(c["x1"]) for c in cl), max(float(c["bottom"]) for c in cl)
        if x1 - x0 < 16 or bottom - top < 8 or x1 - x0 > width * 0.88 or top < height * 0.08:
            continue
        label = "".join(c.get("text", "") for c in sorted(cl, key=lambda c: (round(c["top"] / 8), c["x0"])))
        has_strong_op = any(
            c.get("text", "") in {"=", "∑", "∂", "∇", "∫", "∬", "∮", "≤", "≥", "≠", "≈", "−", "–", "\uf0e5", "\uf053", "\uf0a3", "\uf0b3", "\uf0b6"}
            for c in cl
        )
        has_greek = any(c.get("text", "") in {"μ", "σ", "α", "β", "γ", "δ", "ε", "λ", "θ", "\uf06d", "\uf073", "\uf064"} for c in cl)
        prose_words = [w for w in re.findall(r"[A-Za-z]{4,}", label) if w.lower() not in math_words]
        if (has_strong_op or has_greek) and len(prose_words) <= 2:
            reg = _region((x0, top, x1, bottom), width, height)
            if not any(
                v["kind"] == "equation"
                and abs(v["region"][1] - reg[1]) < 0.035
                and abs(v["region"][0] - reg[0]) < 0.06
                for v in visuals
            ):
                visuals.append({"kind": "equation", "region": reg, "label": label[:250]})

    # Pass 3: Inline mathematical range expressions inside bullet lines (e.g., "μ–σ to μ+σ", "μ–2σ to μ+2σ")
    words = page.extract_words(x_tolerance=2, y_tolerance=3)
    for i, w in enumerate(words):
        if any(g in w["text"] for g in ("μ", "σ", "\uf06d", "\uf073")) and any(op in w["text"] for op in ("–", "-", "−", "+")):
            span = [w]
            if i + 2 < len(words) and words[i + 1]["text"].lower() == "to" and any(g in words[i + 2]["text"] for g in ("μ", "σ", "\uf06d", "\uf073")):
                span.extend([words[i + 1], words[i + 2]])
            sx0 = min(float(item["x0"]) for item in span)
            sx1 = max(float(item["x1"]) for item in span)
            stop = min(float(item["top"]) for item in span)
            sbot = max(float(item["bottom"]) for item in span)
            reg = _region((sx0, stop, sx1, sbot), width, height)
            label = " ".join(item["text"].rstrip(":") for item in span)
            if not any(
                v["kind"] == "equation"
                and abs(v["region"][1] - reg[1]) < 0.025
                and abs(v["region"][0] - reg[0]) < 0.05
                for v in visuals
            ):
                visuals.append({"kind": "equation", "region": reg, "label": label[:250]})

    # PDF image objects are strong evidence for graphs/diagrams. Discard
    # tiny logos and speaker icons, then merge adjacent image fragments.
    candidates = [i for i in page.images if (i["x1"]-i["x0"]) > 65 and
                  (i["bottom"]-i["top"]) > 45 and i["top"] < height*.82]
    if candidates:
        x0 = min(i["x0"] for i in candidates); x1 = max(i["x1"] for i in candidates)
        top = min(i["top"] for i in candidates); bottom = max(i["bottom"] for i in candidates)
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
    # Deduplicate overlapping formula lines (must overlap both vertically AND horizontally).
    unique = []
    for visual in visuals:
        if visual["kind"] == "equation" and any(
            v["kind"] == "equation"
            and abs(v["region"][1] - visual["region"][1]) < 0.018
            and not (visual["region"][0] + visual["region"][2] < v["region"][0]
                     or v["region"][0] + v["region"][2] < visual["region"][0])
            for v in unique
        ):
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
