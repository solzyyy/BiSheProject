import json
import tempfile
import unittest
from pathlib import Path

from scripts.experiments.generate_experiment_matrix import build_experiment_matrix
from scripts.experiments.summarize_experiments import summarize_experiments


class ExperimentWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project_root = Path(tempfile.mkdtemp())
        (self.project_root / "configs").mkdir(parents=True, exist_ok=True)
        base_config = {
            "run_id": "base_demo",
            "input_text": "story.txt",
            "output_root": "runs/base_demo",
            "validate_after_each_step": True,
            "default_step_params": {},
            "steps": {
                "generate-canonical-branch": {"enabled": True},
                "analyze-decision-points": {"enabled": True},
                "generate-branch": {"enabled": True},
                "evaluate-solution": {"enabled": True},
            },
        }
        (self.project_root / "configs" / "base.json").write_text(
            json.dumps(base_config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.project_root / "story.txt").write_text("abc" * 2000, encoding="utf-8")

    def test_build_experiment_matrix_creates_configs_and_manifest(self) -> None:
        output_dir = self.project_root / "configs" / "experiments"
        manifest = build_experiment_matrix(
            project_root=self.project_root,
            base_config_path=self.project_root / "configs" / "base.json",
            output_dir=output_dir,
            branch_counts=[2],
            event_granularities=["medium"],
            decision_densities=["low", "high"],
            text_lengths=["short"],
            repeats=1,
            seed=7,
        )
        self.assertEqual(len(manifest["experiments"]), 2)
        self.assertTrue((output_dir / "manifest.json").exists())
        for exp in manifest["experiments"]:
            cfg_path = self.project_root / exp["config_path"]
            self.assertTrue(cfg_path.exists())
            config_data = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.assertEqual(config_data["run_id"], exp["run_id"])

    def test_summarize_experiments_detects_hash_mismatch(self) -> None:
        output_dir = self.project_root / "configs" / "experiments"
        manifest = build_experiment_matrix(
            project_root=self.project_root,
            base_config_path=self.project_root / "configs" / "base.json",
            output_dir=output_dir,
            branch_counts=[2],
            event_granularities=["medium"],
            decision_densities=["medium"],
            text_lengths=["short"],
            repeats=1,
            seed=11,
        )
        exp = manifest["experiments"][0]
        run_dir = self.project_root / exp["output_root"]
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "experiment_meta.json").write_text(
            json.dumps(
                {
                    "experiment_id": exp["experiment_id"],
                    "run_id": exp["run_id"],
                    "config_hash": "wrong_hash",
                    "status": "passed",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        report = summarize_experiments(
            project_root=self.project_root,
            manifest_path=output_dir / "manifest.json",
            strict=False,
        )
        self.assertEqual(report["summary"]["mismatch_count"], 1)
        self.assertEqual(report["summary"]["included_count"], 0)


if __name__ == "__main__":
    unittest.main()
