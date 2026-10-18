"""Local, word-timed equation grounding. No network or model calls."""
from __future__ import annotations

import re
import json
from pathlib import Path

import pdfplumber


def _box(chars, width, height):
    if not chars:
        return None
    x0 = max(0, min(c['x0'] for c in chars) - 2)
    x1 = min(width, max(c['x1'] for c in chars) + 2)
    y0 = max(0, min(c['top'] for c in chars) - 2)
    y1 = min(height, max(c['bottom'] for c in chars) + 2)
    return [round(x0/width, 5), round(y0/height, 5),
            round((x1-x0)/width, 5), round((y1-y0)/height, 5)]


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
                existing = {term['id'] for term in terms}
                for role, glyphs in (('alpha',{'α','𝛼','\uf061'}),('epsilon',{'ε','ϵ','𝜀'})):
                    matches = [c for c in chars if c['text'] in glyphs]
                    if matches and role not in existing:
                        terms.append({'id':role,'label':role,'region':_box([matches[0]],width,height)})
                betas = sorted((c for c in chars if c['text'] in {'β','𝛽'}),key=lambda c:c['x0'])
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
                sums = [c for c in chars if c['text'] in {'∑','\uf0e5'}]
                if sums:
                    terms.append({'id':'sum','label':'summation','region':_box([sums[0]],width,height)})
                for symbol in ('z','d'):
                    instances=sorted((c for c in chars if c['text'].lower()==symbol),key=lambda c:c['x0'])
                    for position, glyph in enumerate(instances):
                        nearby=[c for c in chars if c['text'] in {'1','2'} and glyph['x1']-1 <= c['x0'] <= glyph['x1']+16
                                and abs(c['top']-glyph['top'])<22]
                        digit=nearby[0]['text'] if nearby else str(position+1) if len(instances)>1 else ''
                        if digit:
                            role=f'{symbol}{digit}'
                            terms.append({'id':role,'label':f'{symbol.upper()} {digit}',
                                          'region':_box([glyph]+nearby[:1],width,height)})
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
        if point.get('reference_kind') == 'equation' or (point.get('action') == 'box' and equations):
            if len(equations)==1:
                recent=(page_number,equations[0],float(point['end'])+20)
            elif equations:
                visual = min(equations,key=lambda v: sum(abs(a-b) for a,b in zip(v['region'],point.get('region') or v['region'])))
                recent=(page_number,visual,float(point['end'])+20)
        if not recent or recent[0]!=page_number or point['start']>recent[2]:
            continue
        equation=recent[1]
        terms={t['id']:t for t in equation.get('terms',[])}
        words=[w for w in point.get('words',[]) if w.get('start') is not None]
        if not words:
            words=[w for segment in transcript_segments if segment['end'] > point['start'] and segment['start'] < point['end']
                   for w in segment.get('words',[]) if w.get('start') is not None and point['start']-.05 <= w['start'] < point['end']+.05]
        # A phrase can straddle transcription segments ("D" / "two").
        # Add lookahead for recognition, but only emit cues starting in this point.
        words += [w for segment in transcript_segments if point['end']-.05 <= segment['start'] <= point['end']+.05
                  for w in segment.get('words',[])[:3] if w.get('start') is not None and w['start'] >= point['end']-.05]
        point_events = len(events)
        for index,role,evidence,confidence in _cues(words):
            if role not in terms: continue
            start=round(float(words[index]['start']),3)
            if start >= point['end']+.01: continue
            if events and events[-1]['point_id']==point['id'] and events[-1]['start']==start: continue
            events.append({'id':f'ef-{len(events)+1:04d}','point_id':point['id'],
                           'page':page_number,'page_id':f'pdf-{page_number}',
                           'equation_id':equation['id'],'term_id':role,
                           'region':terms[role]['region'],'equation_region':equation['region'],
                           'start':start,'source_start':start,'end':min(float(point['end'])+2,recent[2]),
                           'evidence':evidence,'confidence':confidence,'needs_review':False})
        if len(events)==point_events and point.get('reference_kind')=='equation' and point.get('action')=='box':
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
