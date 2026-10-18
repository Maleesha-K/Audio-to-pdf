"""Hand-labeled cues from the supplied optimization narration and slide deck."""
import json
import shutil
import statistics
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.equation_focus import build_focus, active_focus
from backend.gemini_budget import GeminiBudget, MAX_CALLS
from backend.video_export import FrameRenderer, lesson_hash


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / 'data' / 'lessons' / 'sample-optimization'
# (spoken word onset, PDF page, target). Labels were read from transcript.json
# and checked against the source slide, independently of the focus generator.
LABELS = [
    (450.80,4,'sum'),(451.14,4,'z1'),(452.44,4,'d1'),(453.68,4,'z2'),(454.54,4,'d2'),
    (661.84,10,'lhs'),(664.34,10,'old_weight'),(667.44,10,'alpha'),
    (668.54,10,'derivative'),(670.92,10,'derivative'),(674.38,10,'lhs'),
    (675.62,10,'old_weight'),(676.68,10,'alpha'),(678.12,10,'derivative'),
    (683.50,10,'alpha'),(698.10,10,'alpha'),(877.44,22,'beta2'),
    (927.46,23,'beta1'),(929.36,23,'beta2'),(931.90,23,'epsilon'),
    (976.42,24,'alpha'),
]


class EquationFocusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lesson=json.loads((SAMPLE/'lesson.json').read_text(encoding='utf-8'))
        cls.events=build_focus(cls.lesson,SAMPLE/'source.pdf')

    def test_hand_labeled_cues(self):
        matched=[]
        for onset,page,term in LABELS:
            candidates=[event for event in self.events if event['page']==page and
                        abs(event['start']-onset)<=.4]
            if candidates:
                best=min(candidates,key=lambda event:abs(event['start']-onset))
                matched.append((best['term_id']==term,abs(best['start']-onset)))
        coverage=len(matched)/len(LABELS)
        precision=sum(correct for correct,_ in matched)/len(matched) if matched else 0
        onset_error=statistics.median(error for _,error in matched) if matched else 999
        self.assertGreaterEqual(coverage,.8)
        self.assertGreaterEqual(precision,.9)
        self.assertLessEqual(onset_error,.4)
        self.assertGreaterEqual(len(LABELS),20)

    def test_one_box_and_export_invalidation(self):
        self.lesson['equation_focus']=self.events
        cue=active_focus(self.lesson,667.50,'pdf-10')
        self.assertEqual(cue['term_id'],'alpha')
        self.assertIsNone(active_focus(self.lesson,667.50,'pdf-9'))
        alpha_center=cue['region'][0]+cue['region'][2]/2
        self.assertTrue(.20<alpha_center<.26)
        renderer=FrameRenderer(self.lesson,SAMPLE)
        self.assertEqual(renderer.frame(667.50).size,(1280,720))
        before=lesson_hash(self.lesson)
        self.events[0]['region']=[.1,.2,.1,.1]
        self.assertNotEqual(before,lesson_hash(self.lesson))

    def test_budget_cap_and_quota_stop(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as directory:
            budget=GeminiBudget(Path(directory))
            calls=[]
            for _ in range(MAX_CALLS+3):
                budget.run(lambda: calls.append(1) or 'ok')
            self.assertEqual(len(calls),8)
            self.assertEqual(GeminiBudget(Path(directory)).data['calls'],8)
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as directory:
            budget=GeminiBudget(Path(directory))
            def quota(): raise RuntimeError('429 RESOURCE_EXHAUSTED')
            self.assertIsNone(budget.run(quota))
            self.assertTrue(budget.data['quota_exhausted'])
            self.assertIsNone(budget.run(lambda: 'should not run'))
            self.assertEqual(budget.data['calls'],1)

    def test_correction_survives_local_rebuild(self):
        import importlib
        api=importlib.import_module('backend.app')
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as directory:
            root=Path(directory); target=root/'sample-optimization'; target.mkdir()
            for name in ('lesson.json','source.pdf','transcript.json'):
                shutil.copyfile(SAMPLE/name,target/name)
            with mock.patch.object(api,'LESSONS',root):
                event=api._load_lesson('sample-optimization')['equation_focus'][0]
                changed=api.edit_equation_focus('sample-optimization',event['id'],
                    api.EquationFocusEdit(start=event['start']+.7,end=event['end']+.7,term_id='z1'))
                self.assertTrue(changed['edited'])
                result=api.rebuild_equation_focus('sample-optimization')
                saved=[row for row in result['events'] if row['id']==event['id']]
                self.assertEqual(len(saved),1)
                self.assertEqual(saved[0]['term_id'],'z1')
                self.assertAlmostEqual(saved[0]['start'],event['start']+.7,places=2)
                self.assertEqual(result['preserved_corrections'],1)

    def test_old_lesson_opens_without_focus(self):
        import importlib
        api=importlib.import_module('backend.app')
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as directory:
            root=Path(directory); target=root/'legacy'; target.mkdir()
            old=json.loads((SAMPLE/'lesson.json').read_text(encoding='utf-8'))
            old['id']='legacy'; old.pop('equation_focus',None)
            (target/'lesson.json').write_text(json.dumps(old,ensure_ascii=False),encoding='utf-8')
            with mock.patch.object(api,'LESSONS',root):
                loaded=api._load_lesson('legacy')
                self.assertEqual(loaded['equation_focus'],[])
                self.assertEqual(len(loaded['points']),len(old['points']))

    def test_universal_math_glyph_normalization(self):
        from backend.equation_focus import _norm_char
        self.assertEqual(_norm_char('\uf06d'), 'μ')
        self.assertEqual(_norm_char('\uf073'), 'σ')
        self.assertEqual(_norm_char('\uf0e5'), '∑')
        self.assertEqual(_norm_char('\uf053'), '∑')
        self.assertEqual(_norm_char('\uf064'), 'δ')
        self.assertEqual(_norm_char('\uf0b6'), '∂')
        self.assertEqual(_norm_char('\U0001d715'), '∂')
        self.assertEqual(_norm_char('\U0001d464'), 'w')
        self.assertEqual(_norm_char('\u2212'), '-')
        self.assertEqual(_norm_char('\u2013'), '-')

    def test_data_mining_and_calculus_subterm_tracking(self):
        dm_dir = ROOT / 'data' / 'lessons' / '658ebbdb704e'
        calc_dir = ROOT / 'data' / 'lessons' / '764e94c7cee1'
        if not calc_dir.is_dir():
            calc_dir = ROOT / 'tests' / 'fixtures' / '764e94c7cee1'
        if dm_dir.is_dir():
            dm_lesson = json.loads((dm_dir / 'lesson.json').read_text(encoding='utf-8'))
            dm_events = build_focus(dm_lesson, dm_dir / 'source.pdf')
            dm_terms_tracked = {e['term_id'] for e in dm_events}
            # Page 12 has 5 equations (sample mean, population mean, weighted mean, median, empirical formula)
            p12_eqs = [v for v in dm_lesson['pages'][11]['visuals'] if v['kind'] == 'equation']
            self.assertGreaterEqual(len(p12_eqs), 4)
            # Page 14 has IQR, sample variance s^2, and population variance sigma^2
            p14_eqs = [v for v in dm_lesson['pages'][13]['visuals'] if v['kind'] == 'equation']
            self.assertGreaterEqual(len(p14_eqs), 3)
            # Page 17 has the 3 empirical rule intervals (μ±σ, μ±2σ, μ±3σ)
            p17_eqs = [v for v in dm_lesson['pages'][16]['visuals'] if v['kind'] == 'equation']
            self.assertGreaterEqual(len(p17_eqs), 3)
            for expected_role in ('sum', 'x_bar', 'mu', 'weight', 'median_term', 'mode_term', 'iqr', 'q1', 'q3', 's2', 'sigma2', 'mu_sigma_1', 'mu_sigma_2', 'mu_sigma_3', 'z_score', 'cosine'):
                self.assertIn(expected_role, dm_terms_tracked)
        if calc_dir.is_dir():
            calc_lesson = json.loads((calc_dir / 'lesson.json').read_text(encoding='utf-8'))
            calc_events = build_focus(calc_lesson, calc_dir / 'source.pdf')
            calc_terms_tracked = {e['term_id'] for e in calc_events}
            for expected_role in ('sigma', 'integral', 'curl', 'div', 'phi', 'unit_vectors'):
                self.assertIn(expected_role, calc_terms_tracked)


if __name__ == '__main__': unittest.main()
