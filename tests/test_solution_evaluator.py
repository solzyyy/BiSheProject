import json
import tempfile
import unittest
from pathlib import Path

from scripts.review.evaluate_solution import evaluate_solution


class SolutionEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project_root = Path(tempfile.mkdtemp())
        out_dir = self.project_root / "out"
        game_dir = self.project_root / "wangfo" / "game"
        paths_dir = game_dir / "paths"
        endings_dir = game_dir / "endings"

        out_dir.mkdir(parents=True, exist_ok=True)
        paths_dir.mkdir(parents=True, exist_ok=True)
        endings_dir.mkdir(parents=True, exist_ok=True)

        (out_dir / "extract_chain.json").write_text(
            json.dumps(
                [
                    {"event_id": "E1"},
                    {"event_id": "E2"},
                    {"event_id": "E3"},
                    {"event_id": "E4"},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (out_dir / "canonical_branch.json").write_text(
            json.dumps(
                [
                    {"event_id": "E1"},
                    {"event_id": "E2"},
                    {"event_id": "E3"},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (out_dir / "decision_points_analysis.json").write_text(
            json.dumps(
                {
                    "decision_points": [
                        {"id": "D1", "options": ["a", "b"]},
                        {"id": "D2", "options": ["x", "y"]},
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        (paths_dir / "canonical_path.rpy").write_text(
            "label canonical_path:\n    scene bg_temple\n    return\n",
            encoding="utf-8",
        )
        (paths_dir / "branch_1.rpy").write_text(
            "label branch_1:\n    scene bg_street\n    return\n",
            encoding="utf-8",
        )
        (endings_dir / "ending_good.rpy").write_text(
            "label ending_good:\n    return\n",
            encoding="utf-8",
        )
        (game_dir / "images_doubao.rpy").write_text(
            'image bg_temple = "images/bg_temple.png"\nimage bg_street = "images/bg_street.png"\n',
            encoding="utf-8",
        )

    def test_evaluate_solution_generates_expected_metrics(self) -> None:
        report = evaluate_solution(self.project_root)

        self.assertIn("metrics", report)
        self.assertIn("overall", report)
        self.assertEqual(report["metrics"]["extract_event_count"]["value"], 4)
        self.assertEqual(report["metrics"]["canonical_event_count"]["value"], 3)
        self.assertEqual(report["metrics"]["decision_point_count"]["value"], 2)
        self.assertGreaterEqual(report["overall"]["score"], 0.0)
        self.assertLessEqual(report["overall"]["score"], 1.0)

    def test_evaluate_solution_marks_missing_data_in_notes(self) -> None:
        (self.project_root / "out" / "decision_points_analysis.json").unlink()
        report = evaluate_solution(self.project_root)
        notes = "\n".join(report["overall"]["notes"])
        self.assertIn("decision_points_analysis.json", notes)


if __name__ == "__main__":
    unittest.main()
