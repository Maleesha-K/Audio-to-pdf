"""Local, word-timed equation grounding. No network or model calls."""
from __future__ import annotations

import re
import json
from pathlib import Path

import pdfplumber


MATH_GLYPH_MAP = {
    "\uf061": "α", "𝛼": "α",
    "\uf062": "β", "𝛽": "β",
    "\uf067": "γ", "𝛾": "γ",
    "\uf064": "δ", "𝛿": "δ",
    "\uf065": "ε", "𝜀": "ε", "ϵ": "ε",
    "\uf06c": "λ", "𝜆": "λ",
    "\uf06d": "μ", "𝜇": "μ", "µ": "μ",
    "\uf073": "σ", "𝜎": "σ",
    "\uf071": "θ", "𝜃": "θ",
    "\uf066": "φ", "𝜑": "φ",
    "\uf046": "Φ", "\u03a6": "Φ",
    "\uf0e5": "∑", "\uf053": "∑", "\u2211": "∑", "Σ": "∑",
    "\uf0b6": "∂", "\U0001d715": "∂", "\u2202": "∂",
    "\uf0d1": "∇", "\u2207": "∇",
    "\uf0f2": "∫", "\u222b": "∫", "\u222c": "∬", "\u222e": "∮",
    "\uf0a3": "≤", "\u2264": "≤",
    "\uf0b3": "≥", "\u2265": "≥",
    "\uf0b9": "≠", "\u2260": "≠",
    "\uf0bb": "≈", "\u2248": "≈",
    "\uf0b4": "×", "\xd7": "×",
    "\uf0a5": "∞", "\u221e": "∞",
    "\u2212": "-", "\u2013": "-", "−": "-", "–": "-",
    "\xb7": "·", "⋅": "·",
}


def _norm_char(text: str) -> str:
    if not text:
        return ""
    if text in MATH_GLYPH_MAP:
        return MATH_GLYPH_MAP[text]
    cp = ord(text[0])
    # Mathematical Italic/Bold Latin (U+1D434..U+1D467)
    if 0x1D434 <= cp <= 0x1D44D:
        return chr(ord("A") + (cp - 0x1D434))
    if 0x1D44E <= cp <= 0x1D467:
        return chr(ord("a") + (cp - 0x1D44E))
    return text


def _box(chars, width, height):
    if not chars:
        return None
    x0 = max(0, min(c['x0'] for c in chars) - 2)
    x1 = min(width, max(c['x1'] for c in chars) + 2)
    y0 = max(0, min(c['top'] for c in chars) - 2)
    y1 = min(height, max(c['bottom'] for c in chars) + 2)
    return [round(x0/width, 5), round(y0/height, 5),
            round((x1-x0)/width, 5), round((y1-y0)/height, 5)]


def _find_word_chars(chars: list[dict], target: str) -> list[dict]:
    """Find contiguous horizontal characters spelling `target` (case-insensitive)."""
    ordered = sorted(chars, key=lambda c: (round(c['top'] / 6), c['x0']))
    n = len(target)
    for i in range(len(ordered) - n + 1):
        window = ordered[i:i + n]
        if "".join(_norm_char(c['text']).lower() for c in window) == target.lower():
            if max(c['top'] for c in window) - min(c['top'] for c in window) <= 8:
                return window
    return []


def extract_equation_terms(pdf_path: Path, pages: list[dict]) -> None:
    """Attach positioned roles to searchable equations; leave ambiguous scans empty."""
    with pdfplumber.open(pdf_path) as pdf:
        for number, (source, page) in enumerate(zip(pdf.pages, pages), 1):
            width, height = float(source.width), float(source.height)
            for index, visual in enumerate(v for v in page.get('visuals', []) if v['kind'] == 'equation'):
                visual['id'] = f'eq-{number}-{index+1}'
                x, y, w, h = visual['region']
                chars = [c for c in source.chars if c['x0'] >= (x-.004)*width
                         and c['x1'] <= (x+w+.004)*width
                         and c['top'] >= (y-.004)*height
                         and c['bottom'] <= (y+h+.004)*height]
                equals = sorted((c for c in chars if c['text'] == '='), key=lambda c: c['x0'])
                alphas = sorted((c for c in chars if c['text'] in {'α', '\uf061'}), key=lambda c: c['x0'])
                partials = sorted((c for c in chars if c['text'] in {'∂', '\uf0b6'}), key=lambda c: c['x0'])
                weights = sorted((c for c in chars if c['text'].lower() == 'w'), key=lambda c: c['x0'])
                terms = []
                if equals:
                    pivot = equals[0]
                    left = [c for c in chars if c['x1'] < pivot['x0'] and c['x0'] >= min(t['x0'] for t in weights[:1] or chars)]
                    if left:
                        terms.append({'id':'lhs','label':'new weight / left side','region':_box(left,width,height)})
                    terms.append({'id':'equals','label':'equals','region':_box([pivot],width,height)})
                    if alphas and partials and len(weights) >= 2 and weights[0]['x0'] < pivot['x0'] < weights[1]['x0'] < alphas[0]['x0'] < partials[0]['x0']:
                        old = [c for c in chars if pivot['x1'] < c['x0'] < alphas[0]['x0']-1]
                        derivative = [c for c in chars if partials[0]['x0']-1 <= c['x0'] <=
                                      max(t['x1'] for t in chars if t['x0'] >= partials[0]['x0'])]
                        if old: terms.append({'id':'old_weight','label':'old weight / right side','region':_box(old,width,height)})
                        terms.append({'id':'alpha','label':'learning rate alpha','region':_box([alphas[0]],width,height)})
                        if derivative: terms.append({'id':'derivative','label':'partial derivative','region':_box(derivative,width,height)})
                    else:
                        right = [c for c in chars if c['x0'] > pivot['x1']]
                        if right: terms.append({'id':'rhs','label':'right side','region':_box(right,width,height)})
                        # Check for 2D fraction numerator / denominator on the right-hand side
                        pivot_mid_y = 0.5 * (pivot['top'] + pivot['bottom'])
                        num_chars = [c for c in right if c['bottom'] <= pivot_mid_y + 2]
                        den_chars = [c for c in right if c['top'] >= pivot_mid_y - 2]
                        if num_chars and den_chars and (min(c['top'] for c in den_chars) - max(c['top'] for c in num_chars)) >= 8:
                            terms.append({'id':'numerator','label':'numerator','region':_box(num_chars,width,height)})
                            terms.append({'id':'denominator','label':'denominator','region':_box(den_chars,width,height)})
                existing = {term['id'] for term in terms}
                for role, glyphs in (('alpha',{'α','𝛼','\uf061'}),('epsilon',{'ε','ϵ','𝜀','\uf065'})):
                    matches = [c for c in chars if c['text'] in glyphs]
                    if matches and role not in existing:
                        terms.append({'id':role,'label':role,'region':_box([matches[0]],width,height)})
                        existing.add(role)
                betas = sorted((c for c in chars if c['text'] in {'β','𝛽','\uf062'}),key=lambda c:c['x0'])
                for beta_index, beta in enumerate(betas):
                    nearby = [c for c in chars if c['text'] in {'1','2'} and
                              beta['x1']-1 <= c['x0'] <= beta['x1']+14 and
                              abs(c['top']-beta['top']) < 18]
                    digit = nearby[0]['text'] if nearby else str(beta_index+1) if len(betas)>1 else ''
                    role = f'beta{digit}' if digit else 'beta'
                    if role not in existing:
                        terms.append({'id':role,'label':f'beta {digit}'.strip(),
                                      'region':_box([beta]+nearby[:1],width,height)})
                        existing.add(role)
                if partials and 'derivative' not in existing:
                    terms.append({'id':'derivative','label':'partial derivative','region':_box([partials[0]],width,height)})
                    existing.add('derivative')
                sums = [c for c in chars if _norm_char(c['text']) == '∑']
                if sums and 'sum' not in existing:
                    terms.append({'id':'sum','label':'summation','region':_box([sums[0]],width,height)})
                    existing.add('sum')
                for symbol in ('z','d'):
                    instances=sorted((c for c in chars if c['text'].lower()==symbol),key=lambda c:c['x0'])
                    for position, glyph in enumerate(instances):
                        nearby=[c for c in chars if c['text'] in {'1','2'} and glyph['x1']-1 <= c['x0'] <= glyph['x1']+16
                                and abs(c['top']-glyph['top'])<22]
                        digit=nearby[0]['text'] if nearby else str(position+1) if len(instances)>1 else ''
                        if digit:
                            role=f'{symbol}{digit}'
                            if role not in existing:
                                terms.append({'id':role,'label':f'{symbol.upper()} {digit}',
                                              'region':_box([glyph]+nearby[:1],width,height)})
                                existing.add(role)

                # Phase 2 universal mathematical symbols and sub-terms
                label_text = visual.get('label', '')
                # 1. Inline empirical rule intervals (μ–σ to μ+σ, μ–2σ to μ+2σ, μ–3σ to μ+3σ)
                if ('μ' in label_text or '\uf06d' in label_text) and ('σ' in label_text or '\uf073' in label_text) and 'to' in label_text:
                    if '2' in label_text:
                        terms.append({'id':'mu_sigma_2','label':'μ ± 2σ interval','region':visual['region']})
                        existing.add('mu_sigma_2')
                    elif '3' in label_text:
                        terms.append({'id':'mu_sigma_3','label':'μ ± 3σ interval','region':visual['region']})
                        existing.add('mu_sigma_3')
                    else:
                        terms.append({'id':'mu_sigma_1','label':'μ ± σ interval','region':visual['region']})
                        existing.add('mu_sigma_1')

                # 2. Population mean μ, standard deviation σ, and population variance σ²
                mus = sorted((c for c in chars if _norm_char(c['text']) == 'μ'), key=lambda c: c['x0'])
                if mus and 'mu' not in existing:
                    terms.append({'id':'mu','label':'population mean μ','region':_box([mus[0]],width,height)})
                    existing.add('mu')

                sigmas = sorted((c for c in chars if _norm_char(c['text']) == 'σ'), key=lambda c: c['x0'])
                for sig in sigmas:
                    sup2 = [c for c in chars if c['text'] == '2' and sig['x1'] - 2 <= c['x0'] <= sig['x1'] + 12 and abs(c['top'] - sig['top']) < 16]
                    if sup2 and 'sigma2' not in existing:
                        terms.append({'id':'sigma2','label':'population variance σ²','region':_box([sig, sup2[0]],width,height)})
                        existing.add('sigma2')
                    elif 'sigma' not in existing:
                        terms.append({'id':'sigma','label':'standard deviation σ','region':_box([sig],width,height)})
                        existing.add('sigma')

                # 3. Sample variance s² and sample mean x̄
                s_chars = sorted((c for c in chars if _norm_char(c['text']) == 's'), key=lambda c: c['x0'])
                for sc in s_chars:
                    sup2 = [c for c in chars if c['text'] == '2' and sc['x1'] - 2 <= c['x0'] <= sc['x1'] + 12 and abs(c['top'] - sc['top']) < 16]
                    if sup2 and equals and sc['x0'] < equals[0]['x0'] and 's2' not in existing:
                        terms.append({'id':'s2','label':'sample variance s²','region':_box([sc, sup2[0]],width,height)})
                        existing.add('s2')

                x_lhs = [c for c in chars if _norm_char(c['text']) == 'x' and equals and c['x0'] < equals[0]['x0']]
                if x_lhs and sums and 'x_bar' not in existing:
                    terms.append({'id':'x_bar','label':'sample mean x̄','region':_box([x_lhs[0]],width,height)})
                    existing.add('x_bar')

                if weights and sums and not partials and 'weight' not in existing:
                    w_sub = [c for c in chars if weights[0]['x0'] - 1 <= c['x0'] <= weights[0]['x1'] + 10 and abs(c['top'] - weights[0]['top']) < 16]
                    terms.append({'id':'weight','label':'weight w_i','region':_box(w_sub or [weights[0]],width,height)})
                    existing.add('weight')

                # 4. Quartiles & IQR (IQR = Q3 - Q1)
                iqr_chars = _find_word_chars(chars, 'iqr')
                if iqr_chars and 'iqr' not in existing:
                    terms.append({'id':'iqr','label':'IQR','region':_box(iqr_chars,width,height)})
                    existing.add('iqr')
                q_chars = sorted((c for c in chars if _norm_char(c['text']) == 'Q'), key=lambda c: c['x0'])
                for qc in q_chars:
                    sub = [c for c in chars if c['text'] in {'1','3'} and qc['x1'] - 2 <= c['x0'] <= qc['x1'] + 12 and abs(c['top'] - qc['top']) < 18]
                    if sub:
                        q_role = f"q{sub[0]['text']}"
                        if q_role not in existing:
                            terms.append({'id':q_role,'label':f"Q{sub[0]['text']}",'region':_box([qc, sub[0]],width,height)})
                            existing.add(q_role)

                # 5. Mean, Median, Mode words inside formulas
                for word_name, role_id in (('median', 'median_term'), ('mode', 'mode_term'), ('mean', 'mean_term')):
                    w_chars = _find_word_chars(chars, word_name)
                    if w_chars and role_id not in existing:
                        terms.append({'id':role_id,'label':word_name,'region':_box(w_chars,width,height)})
                        existing.add(role_id)

                # 6. Z-score (z = (x - μ)/σ or z_if = ...)
                z_lhs = [c for c in chars if _norm_char(c['text']).lower() == 'z' and equals and c['x0'] < equals[0]['x0']]
                if z_lhs and 'z_score' not in existing:
                    terms.append({'id':'z_score','label':'z-score','region':_box([z_lhs[0]],width,height)})
                    existing.add('z_score')

                # 7. Additional Greek symbols (δ, λ, Φ)
                for role_id, sym, lbl in (('delta', 'δ', 'delta δ'), ('lambda', 'λ', 'lambda λ'), ('phi', 'Φ', 'flux Φ')):
                    matches = [c for c in chars if _norm_char(c['text']) == sym]
                    if matches and role_id not in existing:
                        terms.append({'id':role_id,'label':lbl,'region':_box([matches[0]],width,height)})
                        existing.add(role_id)

                # 8. Calculus & Vector operators (curl, div, surface/line integral, dot product, unit vectors, norm, cosine)
                for op_word, role_id, lbl in (('curl', 'curl', 'curl F'), ('div', 'div', 'div F'), ('cos', 'cosine', 'cosine')):
                    op_chars = _find_word_chars(chars, op_word)
                    if op_chars and role_id not in existing:
                        terms.append({'id':role_id,'label':lbl,'region':_box(op_chars,width,height)})
                        existing.add(role_id)

                int_chars = [c for c in chars if _norm_char(c['text']) in {'∫', '∬', '∮'} or 'cmex' in c.get('fontname', '').lower()]
                if int_chars and 'integral' not in existing:
                    terms.append({'id':'integral','label':'integral','region':_box(int_chars[:2],width,height)})
                    existing.add('integral')

                dots = [c for c in chars if _norm_char(c['text']) == '·']
                if dots and 'dot_product' not in existing:
                    dot_group = [c for c in chars if dots[0]['x0'] - 14 <= c['x0'] <= dots[0]['x1'] + 18 and abs(c['top'] - dots[0]['top']) < 12]
                    terms.append({'id':'dot_product','label':'dot product','region':_box(dot_group or [dots[0]],width,height)})
                    existing.add('dot_product')

                hats = [c for c in chars if c['text'] == '\u02c6']
                if hats and 'unit_vectors' not in existing:
                    uv_group = [c for c in chars if min(h['x0'] for h in hats) - 10 <= c['x0'] <= max(h['x1'] for h in hats) + 10]
                    terms.append({'id':'unit_vectors','label':'unit vector components','region':_box(uv_group or hats,width,height)})
                    existing.add('unit_vectors')

                bars = [c for c in chars if c['text'] == '|']
                if len(bars) >= 4 and 'norm' not in existing:
                    norm_group = [c for c in chars if bars[0]['x0'] - 2 <= c['x0'] <= bars[3]['x1'] + 4 and abs(c['top'] - bars[0]['top']) < 12]
                    terms.append({'id':'norm','label':'vector norm ||d||','region':_box(norm_group or bars[:4],width,height)})
                    existing.add('norm')

                visual['terms'] = terms


def _word(row):
    return re.sub(r'[^a-z0-9]+', '', str(row.get('word','')).lower())


def _cues(words):
    """Return (word index, role, evidence, confidence) in spoken order."""
    tokens = [_word(w) for w in words]
    result = []
    for i, token in enumerate(tokens):
        trail = tokens[i:i+5]
        prev = tokens[max(0,i-3):i]
        if token == 'new' and 'weight' in trail[1:3]:
            result.append((i,'lhs','new weight',.96))
        elif token == 'old' and 'weight' in trail[1:3]:
            result.append((i,'old_weight','old weight',.96))
        elif token in {'alpha','learning'}:
            if token == 'alpha' or 'rate' in trail[1:3]:
                result.append((i,'alpha','alpha / learning rate',.95))
        elif token == 'partial' and 'derivative' in trail[1:3]:
            result.append((i,'derivative','partial derivative',.96))
        elif token == 'beta' and trail[1:2] and trail[1] in {'one','1','two','2'}:
            result.append((i,'beta1' if trail[1] in {'one','1'} else 'beta2','beta '+trail[1],.94))
        elif token == 'epsilon':
            result.append((i,'epsilon','epsilon',.94))
        elif token in {'z','d'} and trail[1:2] and trail[1] in {'one','1','two','2'}:
            result.append((i,token+('1' if trail[1] in {'one','1'} else '2'),token+' '+trail[1],.91))
        elif token in {'sum','summation'}:
            result.append((i,'sum',token,.9))
        elif token == 'w' and 'sub' in trail[1:3]:
            if 'respect' in prev or 'derivative' in prev:
                role = 'derivative'
            elif any(t in trail[2:5] for t in ('minus','one','1')):
                role = 'old_weight'
            elif 'equals' in trail[2:5] or 'equal' in trail[2:5]:
                role = 'lhs'
            else:
                role = None
            if role: result.append((i,role,' '.join(tokens[i:i+4]),.89))
        # Phase 2 universal mathematical cues
        elif token == 'mu' or (token == 'population' and 'mean' in trail[1:3]):
            result.append((i,'mu','population mean μ',.93))
        elif (token == 'sample' and 'mean' in trail[1:3]) or (token == 'x' and 'bar' in trail[1:3]):
            result.append((i,'x_bar','sample mean x̄',.93))
        elif token in {'weighted','weights'} and ('mean' in trail[1:4] or 'average' in trail[1:4] or 'sum' in trail[1:4]):
            result.append((i,'weight','weighted mean w_i',.91))
        elif (token == 'sigma' and 'squared' in trail[1:3]) or (token == 'population' and 'variance' in trail[1:3]):
            result.append((i,'sigma2','population variance σ²',.94))
        elif (token == 's' and 'squared' in trail[1:3]) or (token == 'sample' and 'variance' in trail[1:3]):
            result.append((i,'s2','sample variance s²',.94))
        elif token == 'sigma' or (token == 'standard' and any(t in trail[1:3] for t in ('deviation','deviations'))):
            if any(t in prev[-2:] for t in ('one','1')):
                result.append((i,'mu_sigma_1','1 standard deviation (μ±σ)',.93))
            elif any(t in prev[-2:] for t in ('two','2')):
                result.append((i,'mu_sigma_2','2 standard deviations (μ±2σ)',.93))
            elif any(t in prev[-2:] for t in ('three','3')):
                result.append((i,'mu_sigma_3','3 standard deviations (μ±3σ)',.93))
            else:
                result.append((i,'sigma','standard deviation σ',.92))
        elif token in {'68','sixty'} and (token == '68' or 'eight' in trail[1:3]):
            result.append((i,'mu_sigma_1','68% within μ±σ',.91))
        elif token in {'95','ninety'} and (token == '95' or 'five' in trail[1:3]):
            result.append((i,'mu_sigma_2','95% within μ±2σ',.91))
        elif token in {'99','997'} or (token == 'ninety' and 'nine' in trail[1:3]):
            result.append((i,'mu_sigma_3','99.7% within μ±3σ',.91))
        elif token in {'iqr','interquartile'}:
            result.append((i,'iqr','interquartile range IQR',.94))
        elif token in {'q3','75th'} or (token == 'q' and trail[1:2] == ['3']) or (token == 'third' and 'quartile' in trail[1:3]):
            result.append((i,'q3','third quartile Q3',.93))
        elif token in {'q1','25th'} or (token == 'q' and trail[1:2] == ['1']) or (token == 'first' and 'quartile' in trail[1:3]):
            result.append((i,'q1','first quartile Q1',.93))
        elif token == 'median':
            result.append((i,'median_term','median',.90))
        elif token == 'mode':
            result.append((i,'mode_term','mode',.90))
        elif token == 'zscore' or (token == 'z' and 'score' in trail[1:3]):
            result.append((i,'z_score','z-score',.94))
        elif token == 'delta':
            result.append((i,'delta','delta δ',.92))
        elif token == 'lambda':
            result.append((i,'lambda','lambda λ',.93))
        elif token in {'phi','flux'}:
            result.append((i,'phi','flux Φ',.91))
        elif token == 'curl':
            result.append((i,'curl','curl F',.94))
        elif token in {'divergence','div'}:
            result.append((i,'div','div F',.93))
        elif (token in {'surface','line','double','triple'} and 'integral' in trail[1:3]) or (token == 'integral' and not (prev[-1:] and prev[-1] in {'surface','line','double','triple'})):
            result.append((i,'integral','integral',.92))
        elif (token == 'dot' and 'product' in trail[1:3]) or (token == 'normal' and 'vector' in trail[1:3]):
            result.append((i,'dot_product','dot product · n',.91))
        elif token == 'vector' and 'field' in trail[1:3]:
            result.append((i,'unit_vectors','vector field F',.89))
        elif token in {'norm','magnitude'}:
            result.append((i,'norm','vector norm ||d||',.91))
        elif token == 'cosine':
            result.append((i,'cosine','cosine similarity',.93))
        elif token == 'numerator':
            result.append((i,'numerator','numerator',.91))
        elif token == 'denominator' or (token == 'divided' and 'by' in trail[1:3]):
            result.append((i,'denominator','denominator',.90))
    return result


def build_focus(lesson: dict, pdf_path: Path | None = None) -> list[dict]:
    pages = lesson['pages']
    if pdf_path is not None:
        extract_equation_terms(pdf_path, pages)
    transcript_segments = []
    if pdf_path is not None and (pdf_path.parent / 'transcript.json').is_file():
        transcript_segments = json.loads((pdf_path.parent / 'transcript.json').read_text(encoding='utf-8')).get('segments', [])
    events = []
    recent = None
    for point in lesson['points']:
        if point.get('page_id',f"pdf-{point.get('page',1)}").startswith('wb-'):
            recent = None
            continue
        page_number = point.get('page',1)
        page = pages[page_number-1] if 1 <= page_number <= len(pages) else None
        if not page: continue
        equations = [v for v in page.get('visuals',[]) if v.get('kind')=='equation']
        if not equations:
            continue
        if point.get('reference_kind') == 'equation' or (point.get('action') == 'box' and equations):
            if len(equations)==1:
                recent=(page_number,equations[0],float(point['end'])+20)
            elif equations:
                visual = min(equations,key=lambda v: sum(abs(a-b) for a,b in zip(v['region'],point.get('region') or v['region'])))
                recent=(page_number,visual,float(point['end'])+20)
        words=[w for w in point.get('words',[]) if w.get('start') is not None]
        if not words:
            words=[w for segment in transcript_segments if segment['end'] > point['start'] and segment['start'] < point['end']
                   for w in segment.get('words',[]) if w.get('start') is not None and point['start']-.05 <= w['start'] < point['end']+.05]
        # A phrase can straddle transcription segments ("D" / "two").
        # Add lookahead for recognition, but only emit cues starting in this point.
        words += [w for segment in transcript_segments if point['end']-.05 <= segment['start'] <= point['end']+.05
                  for w in segment.get('words',[])[:3] if w.get('start') is not None and w['start'] >= point['end']-.05]
        cues = _cues(words)
        active_recent = recent if (recent and recent[0]==page_number and point['start']<=recent[2]) else None
        if not active_recent and not cues:
            continue
        point_events = len(events)
        for index,role,evidence,confidence in cues:
            target_eq = None
            if active_recent and any(t['id'] == role for t in active_recent[1].get('terms', [])):
                target_eq = active_recent[1]
            else:
                matching_eqs = [eq for eq in equations if any(t['id'] == role for t in eq.get('terms', []))]
                if matching_eqs:
                    ref_region = point.get('region') or (active_recent[1]['region'] if active_recent else None)
                    if ref_region:
                        target_eq = min(matching_eqs, key=lambda v: abs(v['region'][1] - ref_region[1]) + 0.5 * abs(v['region'][0] - ref_region[0]))
                    else:
                        target_eq = matching_eqs[0]
                    recent = (page_number, target_eq, float(point['end']) + 20)
                    active_recent = recent
            if not target_eq:
                continue
            terms = {t['id']:t for t in target_eq.get('terms',[])}
            if role not in terms: continue
            start=round(float(words[index]['start']),3)
            if start >= point['end']+.01: continue
            if events and events[-1]['page']==page_number and abs(events[-1]['start']-start)<0.05: continue
            events.append({'id':f'ef-{len(events)+1:04d}','point_id':point['id'],
                           'page':page_number,'page_id':f'pdf-{page_number}',
                           'equation_id':target_eq['id'],'term_id':role,
                           'region':terms[role]['region'],'equation_region':target_eq['region'],
                           'start':start,'source_start':start,'end':min(float(point['end'])+2,recent[2]),
                           'evidence':evidence,'confidence':confidence,'needs_review':False})
        if len(events)==point_events and active_recent and point.get('reference_kind')=='equation' and point.get('action')=='box':
            equation = active_recent[1]
            events.append({'id':f'ef-{len(events)+1:04d}','point_id':point['id'],
                           'page':page_number,'page_id':f'pdf-{page_number}',
                           'equation_id':equation['id'],'term_id':'whole','region':equation['region'],
                           'equation_region':equation['region'],'start':point['start'],
                           'source_start':point['start'],'end':point['end'],
                           'evidence':point['text'][:120],'confidence':.35,'needs_review':True})
    events.sort(key=lambda e:e['start'])
    for i,event in enumerate(events):
        if i+1<len(events) and events[i+1]['page']==event['page'] and events[i+1]['start']<event['end']:
            event['end']=events[i+1]['start']
        event['end']=round(max(event['start']+.05,event['end']),3)
    return events


def active_focus(lesson: dict, second: float, page_id: str) -> dict | None:
    for event in reversed(lesson.get('equation_focus',[])):
        if event['start']<=second<event['end'] and event['page_id']==page_id and not (lesson.get('grounding_version',0)>=2 and event.get('needs_review')):
            return event
    return None
