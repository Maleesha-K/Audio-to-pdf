"""Human-checked lecture landmarks for the local PDF grounding pipeline."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.grounding import build_document_map, align_document
from backend.analysis import make_points
from backend.app import _merge_rebuilt_points

ROOT = Path(__file__).resolve().parents[1]
LESSONS = ROOT / "data" / "lessons"
MODEL = ROOT / "data" / "models"

# Distinct timed passages checked against the spoken topics and original pages.
CALCULUS_PAGES = {
    1: [160,200,260,320,380,450,500,560,620,720,840,950],
    2: [985,1010,1040,1100,1140,1190,1230,1270,1310,1370,1430,1490],
    3: [1560,1600,1640,1680,1730,1750,1780],
    4: [1830,1860,1890,1920,1950,1980,2000,2010],
}
OPTIMIZATION_PAGES = [
    (40,1),(85,1),(130,1),(175,1),(220,3),(265,3),(310,3),(355,3),(400,3),
    (445,4),(490,4),(535,5),(580,6),(625,6),(661.84,10),(670,10),(715,12),
    (760,13),(805,14),(850,15),(877.44,22),(927.46,23),(940,23),(976.42,24),(1030,26),
]
# A block label describes source content, rather than an implementation block ID.
BLOCK_LABELS = {
    "764e94c7cee1": [
        (160,"7.1 Stokes"),(200,"Definition 7.1.1"),(985,"7.2 The Divergence"),
        (1010,"Definition 7.2.1"),(1080,"Theorem 7.2.1"),
        (1455,"Divergence Viewed as Flux Density"),(1490,"Suppose that G"),
        (1750,"Exercises for Lecture 7"),(1780,"2. Use Stokes"),
        (1830,"5. Use the Divergence"),(1950,"8. Determine whether"),
    ],
    "sample-optimization": [
        (180,"A Feed Forward Neural Network"),(445,"Squared difference"),
        (535,"error (E) is a function"),(703,"Gradient Decent for Learning Parameters"),
        (743,"Stochastic Gradient Decent"),(791,"Mini-batch Gradient Decent"),
        (941,"Learning Rate Decay"),(1030,"Hyperparameter Tuning: Grid"),
    ],
}


def _at(points, second):
    return next((p for p in points if p["start"] <= second < p["end"]), None)


class GroundingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (MODEL / "models--Qdrant--bge-small-en-v1.5-onnx-Q").exists():
            raise unittest.SkipTest("Local FastEmbed model has not been downloaded")
        cls.results = {}
        for name in ("764e94c7cee1", "sample-optimization"):
            folder = (LESSONS / name) if (LESSONS / name / "lesson.json").exists() else (ROOT / "tests" / "fixtures" / name)
            lesson = json.loads((folder / "lesson.json").read_text(encoding="utf-8"))
            transcript = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
            build_document_map(lesson["pages"])
            cls.results[name] = (lesson["pages"],align_document(make_points(transcript["segments"]),lesson["pages"],MODEL))

    def test_sixty_four_page_landmarks(self):
        labels = [("764e94c7cee1",t,page) for page,times in CALCULUS_PAGES.items() for t in times]
        labels += [("sample-optimization",t,page) for t,page in OPTIMIZATION_PAGES]
        self.assertGreaterEqual(len(labels),60)
        found = [(name,t,expected,_at(self.results[name][1],t)) for name,t,expected in labels]
        accuracy = sum(point is not None and point["page"]==expected for _,_,expected,point in found)/len(found)
        self.assertGreaterEqual(accuracy,.90,[(name,t,expected,p and p["page"])
                                                for name,t,expected,p in found if not p or p["page"]!=expected])

    def test_verified_block_precision_and_coverage(self):
        checked = 0;correct = 0;emitted = 0
        for name,labels in BLOCK_LABELS.items():
            pages,points=self.results[name]
            blocks={b["id"]:b for page in pages for b in page["blocks"]}
            for second,phrase in labels:
                checked+=1
                point=_at(points,second)
                if point and point["action"]!="hold":
                    emitted+=1
                    block=blocks.get(point["block_id"],{})
                    correct+=phrase.lower() in block.get("text","").lower()
        self.assertGreaterEqual(emitted/checked,.80)
        self.assertGreaterEqual(correct/max(emitted,1),.80)

    def test_heading_map_and_unsupported_audio(self):
        pages,points=self.results["764e94c7cee1"]
        self.assertEqual(pages[1]["title"],"7.2 The Divergence Theorem")
        self.assertEqual(pages[2]["title"],"Exercises for Lecture 7")
        self.assertEqual(_at(points,985)["page"],2)
        self.assertEqual(_at(points,1750)["page"],3)
        self.assertEqual(_at(points,1830)["page"],4)
        # A late speculative physics detour has no supported PDF block.
        unsupported=_at(points,2137)
        if unsupported:
            self.assertEqual(unsupported["action"],"hold")

    def test_unreadable_scan_abstains(self):
        pages=[{"number":1,"title":"Page 1","text":"","words":[],"visuals":[],"text_quality":"unreadable"}]
        build_document_map(pages)
        with mock.patch.dict("os.environ",{"AUDIO_TO_PDF_DISABLE_EMBEDDINGS":"1"}):
            points=align_document([{"start":0,"end":5,"text":"the theorem explains divergence"}],pages)
        self.assertEqual(points[0]["action"],"hold")
        self.assertTrue(points[0]["needs_review"])

    def test_back_reference_to_previous_section(self):
        pages=[{"number":1,"title":"","text":"","words":[
            {"text":"1.1","x":.1,"y":.2,"w":.06,"h":.02},
            {"text":"Introduction","x":.18,"y":.2,"w":.2,"h":.02}],"visuals":[]},
            {"number":2,"title":"","text":"","words":[
            {"text":"1.2","x":.1,"y":.2,"w":.06,"h":.02},
            {"text":"Examples","x":.18,"y":.2,"w":.2,"h":.02}],"visuals":[]}]
        build_document_map(pages)
        rows=[{"start":0,"end":3,"text":"section 1.1 Introduction"},
              {"start":3,"end":6,"text":"now section 1.2 Examples"},
              {"start":6,"end":9,"text":"back to section 1.1 Introduction"}]
        with mock.patch.dict("os.environ",{"AUDIO_TO_PDF_DISABLE_EMBEDDINGS":"1"}):
            result=align_document(rows,pages)
        self.assertEqual([p["page"] for p in result],[1,2,1])

    def test_alignment_rebuild_keeps_edits_and_whiteboards(self):
        old = {"pages":[{"number":1,"blocks":[{"id":"pdf-1-b1","text":"Gradient descent rule"}]}],
               "points":[
                   {"id":"p0001","start":0,"end":4,"page":1,"page_id":"pdf-1",
                    "block_id":"pdf-1-b1","action":"underline","region":[.1,.2,.4,.01],
                    "edited":True,"needs_review":False},
                   {"id":"p0002","start":20,"end":25,"page":1,"page_id":"pdf-1",
                    "block_id":"pdf-1-b1","action":"box","region":[.1,.2,.4,.2],
                    "edited":True,"needs_review":False},
                   {"id":"p0001","start":5,"end":8,"page_id":"wb-hill",
                    "whiteboard_id":"wb-hill","action":"whiteboard"}]}
        generated=[{"id":"p0001","start":0,"end":5,"page":1,"page_id":"pdf-1",
                    "block_id":None,"action":"hold","region":None}]
        pages=[{"number":1,"blocks":[{"id":"pdf-1-b9","text":"Gradient descent rule",
                                        "parent_id":None}]}]
        updated=_merge_rebuilt_points(old,generated,pages)
        self.assertEqual(len(updated),3)
        self.assertEqual(len({p["id"] for p in updated}),3)
        self.assertEqual(updated[0]["block_id"],"pdf-1-b9")
        self.assertEqual(updated[0]["action"],"underline")
        self.assertEqual(updated[1]["whiteboard_id"],"wb-hill")
        self.assertTrue(updated[2]["needs_review"])
        self.assertEqual(updated[2]["action"],"box")

    def test_rebuild_stale_block_enters_review(self):
        old={"pages":[{"number":1,"blocks":[{"id":"pdf-1-b1","text":"Old formula"}]}],
             "points":[{"id":"p1","start":0,"end":3,"page":1,"page_id":"pdf-1",
                        "block_id":"pdf-1-b1","action":"box","edited":True,"needs_review":False}]}
        fresh=[{"id":"p1","start":0,"end":3,"page":1,"page_id":"pdf-1","action":"hold"}]
        pages=[{"number":1,"blocks":[{"id":"pdf-1-b1","text":"Different formula"}]}]
        result=_merge_rebuilt_points(old,fresh,pages)
        self.assertIsNone(result[0]["block_id"])
        self.assertTrue(result[0]["needs_review"])

    def test_empty_alignment_is_safe(self):
        self.assertEqual(align_document([],[]),[])

    def test_repeated_slide_headings_and_multiline_titles(self):
        pages, points = self.results["sample-optimization"]
        self.assertEqual(pages[0]["title"], "Optimization for Neural Networks")
        for pnum in (6, 7, 8, 9):
            self.assertEqual(pages[pnum - 1]["title"], "Gradient Decent")
        for pnum in (16, 17, 18, 19):
            self.assertEqual(pages[pnum - 1]["title"], "Exponential Moving Average (EMA)")
        for pnum in (27, 28):
            self.assertEqual(pages[pnum - 1]["title"], "Hyperparameter Tuning: Coarse to Fine Granularity")
        self.assertEqual(_at(points, 490)["page"], 4)
        self.assertEqual(_at(points, 535)["page"], 5)
        self.assertEqual(_at(points, 580)["page"], 6)

    def test_pause_bridging_and_data_integrity(self):
        for name, (pages, points) in self.results.items():
            self.assertEqual(len({p["id"] for p in points}), len(points))
            page_blocks = {p["number"]: {b["id"] for b in p["blocks"]} for p in pages}
            for idx, p in enumerate(points):
                self.assertLess(p["start"], p["end"])
                if idx > 0:
                    self.assertGreaterEqual(p["start"], points[idx - 1]["start"])
                if p.get("block_id") is not None:
                    self.assertIn(p["block_id"], page_blocks[p["page"]])
                if p.get("region") is not None:
                    x, y, w, h = p["region"]
                    self.assertTrue(0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1)

    def test_forward_page_flow_and_within_page_monotonic_bullets(self):
        if not (LESSONS / "bc4fc246efc9" / "lesson.json").exists():
            return
        lesson = json.loads((LESSONS / "bc4fc246efc9" / "lesson.json").read_text(encoding="utf-8"))
        transcript = json.loads((LESSONS / "bc4fc246efc9" / "transcript.json").read_text(encoding="utf-8"))
        build_document_map(lesson["pages"])
        points = align_document(make_points(transcript["segments"]), lesson["pages"], MODEL)
        blocks_by_id = {b["id"]: b for p in lesson["pages"] for b in p["blocks"]}

        # Verify page-level forward progression without jumping back to Page 2 outline
        expected_pages = [
            (350, 4), (481, 4), (520, 5), (566.86, 6), (590, 7),
            (690, 8), (745, 9), (776, 11), (800, 12), (870, 13),
            (905, 14), (1146, 49), (1170, 50), (1446, 64), (1472, 65),
        ]
        for sec, exp_page in expected_pages:
            pt = _at(points, sec)
            self.assertIsNotNone(pt, f"Missing point at {sec}s")
            self.assertEqual(pt["page"], exp_page, f"At {sec}s expected page {exp_page}, got {pt['page']}")

        # Verify within-page top-to-bottom monotonic flow on Page 4 (341s..518s)
        page4_underlined = [
            pt for pt in points
            if pt["page"] == 4 and pt["action"] == "underline" and pt.get("block_id")
        ]
        y_coords = [blocks_by_id[pt["block_id"]]["region"][1] for pt in page4_underlined]
        self.assertGreaterEqual(len(page4_underlined), 5)
        self.assertEqual(y_coords, sorted(y_coords), f"Page 4 underlines did not flow top-to-bottom: {y_coords}")
        res_pt = _at(points, 481)
        self.assertEqual(res_pt["action"], "underline")
        self.assertIn("resolution", blocks_by_id[res_pt["block_id"]]["text"].lower())

    def test_rule_mining_no_premature_page_freeze(self):
        if not (LESSONS / "5c1be348c21d" / "lesson.json").exists():
            return
        lesson = json.loads((LESSONS / "5c1be348c21d" / "lesson.json").read_text(encoding="utf-8"))
        transcript = json.loads((LESSONS / "5c1be348c21d" / "transcript.json").read_text(encoding="utf-8"))
        build_document_map(lesson["pages"])
        points = align_document(make_points(transcript["segments"]), lesson["pages"], MODEL)

        # Verify 07:27-23:00 progresses smoothly through Pages 8, 9, 10, 11, 12, 14, 15
        # and also verify FP-tree duplicate frame skipping (Pages 20->22->24->26->48->49->50->51)
        # and constraints vs summary disambiguation (Pages 66->68->69).
        expected_landmarks = [
            (255, 5),    # 04:15 Session Outcomes
            (320, 7),    # 05:20 Defining Frequent Pattern Analysis
            (455, 8),    # 07:35 Importance of Frequent Pattern Mining (was prematurely jumping to Page 15!)
            (550, 9),    # 09:10 Basic Concepts: Support and Confidence
            (850, 10),   # 14:10 Downward Closure Property
            (980, 11),   # 16:20 Scalable Methods Overview
            (1090, 12),  # 18:10 Apriori: Candidate Generation-and-Test
            (1220, 14),  # 20:20 Important Details of Apriori
            (1365, 15),  # 22:45 Challenges of Frequent Pattern Mining
            (1875, 20),  # 31:15 Construct FP-tree
            (1886, 22),  # 31:26 Step 1
            (1900, 24),  # 31:40 Step 2
            (1940, 26),  # 32:20 Step 3
            (2000, 48),  # 33:20 Benefits of the FP-tree Structure
            (2095, 49),  # 34:55 How to Mine Frequent Patterns Using FP-tree
            (2140, 50),  # 35:40 Scalability: FP-Growth vs. Apriori
            (2235, 51),  # 37:15 Why Is FP-Growth the Winner?
            (2910, 66),  # 48:30 Constraints in Data Mining (was prematurely jumping to Page 69!)
            (2990, 68),  # 49:50 Constrained Mining vs. Naive Mining
            (3100, 69),  # 51:40 Frequent-Pattern Mining: Summary
        ]
        for sec, exp_page in expected_landmarks:
            pt = _at(points, sec)
            self.assertIsNotNone(pt, f"Missing point at {sec}s")
            self.assertEqual(pt["page"], exp_page, f"At {sec}s expected page {exp_page}, got {pt['page']}")


if __name__ == "__main__": unittest.main()


