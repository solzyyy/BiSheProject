import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.review.build_manual_review_checklist import build_manual_review_checklist


class ManualReviewChecklistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project_root = Path(tempfile.mkdtemp())
        out_dir = self.project_root / "out"
        paths_dir = self.project_root / "wangfo" / "game" / "paths"
        out_dir.mkdir(parents=True, exist_ok=True)
        paths_dir.mkdir(parents=True, exist_ok=True)

        (out_dir / "extract_chain.json").write_text(
            json.dumps(
                [
                    {"event_id": "E1", "summary": "王佛进入寺庙", "characters": ["王佛"]},
                    {"event_id": "E2", "summary": "发现密道", "characters": ["王佛", "僧人"]},
                    {"event_id": "E3", "summary": "遭遇守卫", "characters": ["守卫"]},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (out_dir / "decision_points_analysis.json").write_text(
            json.dumps(
                {
                    "decision_points": [
                        {"decision_id": "D1", "description": "走正门还是密道", "options": ["正门", "密道"]},
                        {"decision_id": "D2", "description": "是否救人", "options": ["救", "不救"]},
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (paths_dir / "branch_1.rpy").write_text(
            "label branch_1:\n    menu:\n        \"走正门\":\n            jump ending_a\n",
            encoding="utf-8",
        )

    def test_build_manual_review_checklist_writes_csv(self) -> None:
        output_csv = self.project_root / "docs" / "manual_review_template.csv"
        stats = build_manual_review_checklist(
            project_root=self.project_root,
            output_csv=output_csv,
            event_sample_size=2,
            decision_sample_size=2,
            path_sample_size=1,
            random_seed=7,
        )

        self.assertTrue(output_csv.exists())
        self.assertEqual(stats["event_rows"], 2)
        self.assertEqual(stats["decision_rows"], 2)
        self.assertEqual(stats["path_rows"], 1)
        self.assertEqual(stats["total_rows"], 5)

        with output_csv.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 5)
        self.assertIn("sample_id", rows[0])
        self.assertIn("scriptability_auto", rows[0])
        self.assertIn("scriptability_human", rows[0])
        self.assertIn("auto_note", rows[0])


if __name__ == "__main__":
    unittest.main()
