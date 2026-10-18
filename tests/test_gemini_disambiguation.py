"""Unit tests for the Gemini grounding disambiguation pass and deterministic guardrails."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.gemini_budget import GeminiBudget
from backend.grounding import align_document, disambiguate_with_gemini


ROOT = Path(__file__).resolve().parents[1]


def _sample_pages() -> list[dict]:
    return [
        {
            "number": 1,
            "title": "Attribute Types",
            "text": "Attribute Types\nDiscrete Attribute: Has a finite or countably infinite set of values\nContinuous Attribute: Has real numbers as attribute values",
            "words": [
                {"text": "Discrete", "x": 0.10, "y": 0.30, "w": 0.12, "h": 0.03},
                {"text": "Attribute:", "x": 0.23, "y": 0.30, "w": 0.12, "h": 0.03},
                {"text": "Continuous", "x": 0.10, "y": 0.55, "w": 0.14, "h": 0.03},
                {"text": "Attribute:", "x": 0.25, "y": 0.55, "w": 0.12, "h": 0.03},
            ],
            "blocks": [
                {
                    "id": "b-1-1",
                    "page": 1,
                    "kind": "heading",
                    "text": "Attribute Types",
                    "region": [0.10, 0.08, 0.40, 0.05],
                },
                {
                    "id": "b-1-2",
                    "page": 1,
                    "kind": "paragraph",
                    "text": "Discrete Attribute: Has a finite or countably infinite set of values",
                    "region": [0.10, 0.30, 0.65, 0.04],
                },
                {
                    "id": "b-1-3",
                    "page": 1,
                    "kind": "paragraph",
                    "text": "Continuous Attribute: Has real numbers as attribute values",
                    "region": [0.10, 0.55, 0.62, 0.04],
                },
            ],
            "visuals": [],
        }
    ]


class GeminiDisambiguationTest(unittest.TestCase):
    def test_paraphrased_passage_disambiguated_and_underlined(self):
        pages = _sample_pages()
        points = [
            {
                "id": "p0001",
                "start": 10.0,
                "end": 18.0,
                "text": "When dealing with sensor measurements like temperature or weight, they take on arbitrary floating point fractions within an interval.",
                "words": [],
            }
        ]
        fake_response = mock.MagicMock()
        fake_response.text = json.dumps(
            {
                "chosen_block_id": "b-1-3",
                "confidence": 0.94,
                "reasoning": "Sensor measurements taking arbitrary fractional values in an interval define Continuous Attributes.",
            }
        )
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmpdir:
            budget = GeminiBudget(Path(tmpdir))
            with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), mock.patch.object(
                budget, "run", return_value=fake_response
            ):
                aligned = align_document(points, pages, model_dir=None, budget=budget)
                self.assertEqual(len(aligned), 1)
                item = aligned[0]
                self.assertEqual(item["block_id"], "b-1-3")
                self.assertEqual(item["action"], "underline")
                self.assertIsNotNone(item["region"])
                self.assertFalse(item["needs_review"])
                self.assertEqual(item["evidence"]["matcher"], "gemini-disambiguated")
                self.assertEqual(
                    item["evidence"]["gemini_disambiguation"]["chosen_block_id"],
                    "b-1-3",
                )

    def test_hallucinated_block_id_rejected_by_guardrail(self):
        pages = _sample_pages()
        candidates = pages[0]["blocks"][1:]
        fake_response = mock.MagicMock()
        fake_response.text = json.dumps(
            {
                "chosen_block_id": "b-99-invented",
                "confidence": 0.99,
                "reasoning": "Invented block ID.",
            }
        )
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmpdir:
            budget = GeminiBudget(Path(tmpdir))
            with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), mock.patch.object(
                budget, "run", return_value=fake_response
            ):
                res = disambiguate_with_gemini(
                    "Some paraphrased explanation",
                    candidates,
                    page_title="Attribute Types",
                    budget=budget,
                )
                self.assertIsNone(res)

    def test_explicit_abstention_records_evidence_and_holds_page(self):
        pages = _sample_pages()
        points = [
            {
                "id": "p0001",
                "start": 20.0,
                "end": 26.0,
                "text": "Before we continue, remember that the midterm exam logistics will be posted on Friday.",
                "words": [],
            }
        ]
        fake_response = mock.MagicMock()
        fake_response.text = json.dumps(
            {
                "chosen_block_id": "none",
                "confidence": 0.91,
                "reasoning": "Speaker is discussing exam logistics not present on the slide.",
            }
        )
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmpdir:
            budget = GeminiBudget(Path(tmpdir))
            with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), mock.patch.object(
                budget, "run", return_value=fake_response
            ):
                aligned = align_document(points, pages, model_dir=None, budget=budget)
                item = aligned[0]
                self.assertEqual(item["action"], "hold")
                self.assertIsNone(item["block_id"])
                self.assertTrue(item["needs_review"])
                self.assertEqual(item["evidence"]["matcher"], "gemini-abstained")
                self.assertEqual(
                    item["evidence"]["gemini_disambiguation"]["status"],
                    "abstained",
                )


if __name__ == "__main__":
    unittest.main()
