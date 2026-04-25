import json
import tempfile
import unittest
from pathlib import Path

from src.pipeline.config import PipelineConfigError, load_pipeline_config


class PipelineConfigTests(unittest.TestCase):
    def write_config(self, payload: dict) -> Path:
        tmpdir = Path(tempfile.mkdtemp())
        path = tmpdir / "config.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def test_load_valid_config(self) -> None:
        config_path = self.write_config(
            {
                "run_id": "demo_wangfo",
                "input_text": "王佛脱险记.txt",
                "steps": {
                    "extract-events": {"enabled": True},
                    "generate-all-paths": {
                        "enabled": True,
                        "params": {"canonical": "out/canonical_branch_chronological.json"},
                    },
                },
            }
        )

        config = load_pipeline_config(config_path)

        self.assertEqual(config.run_id, "demo_wangfo")
        self.assertEqual(config.input_text, "王佛脱险记.txt")
        self.assertTrue(config.steps["extract-events"].enabled)
        self.assertEqual(
            config.steps["generate-all-paths"].params["canonical"],
            "out/canonical_branch_chronological.json",
        )

    def test_missing_required_fields_raise_error(self) -> None:
        config_path = self.write_config({"run_id": "demo_only"})

        with self.assertRaises(PipelineConfigError):
            load_pipeline_config(config_path)

    def test_default_values_are_applied(self) -> None:
        config_path = self.write_config(
            {
                "run_id": "demo_defaults",
                "input_text": "王佛脱险记.txt",
                "steps": {
                    "extract-events": {},
                },
            }
        )

        config = load_pipeline_config(config_path)

        self.assertEqual(config.output_root, "runs/demo_defaults")
        self.assertFalse(config.validate_after_each_step)
        self.assertTrue(config.steps["extract-events"].enabled)
        self.assertEqual(config.steps["extract-events"].params, {})

    def test_step_level_values_override_global_defaults(self) -> None:
        config_path = self.write_config(
            {
                "run_id": "demo_overrides",
                "input_text": "王佛脱险记.txt",
                "default_step_params": {
                    "output_dir": "out/default",
                    "max_concurrent": 3,
                },
                "steps": {
                    "generate-content": {
                        "params": {
                            "output_dir": "out/custom",
                        }
                    }
                },
            }
        )

        config = load_pipeline_config(config_path)

        self.assertEqual(config.steps["generate-content"].params["output_dir"], "out/custom")
        self.assertEqual(config.steps["generate-content"].params["max_concurrent"], 3)

    def test_complete_all_path_events_step_is_loaded(self) -> None:
        config_path = self.write_config(
            {
                "run_id": "demo_wangfo",
                "input_text": "王佛脱险记.txt",
                "steps": {
                    "extract-events": {"enabled": True},
                    "complete-all-path-events": {
                        "enabled": True,
                        "params": {"input_dir": "out/all_paths"},
                    },
                },
            }
        )

        config = load_pipeline_config(config_path)

        self.assertIn("complete-all-path-events", config.steps)
        self.assertEqual(
            config.steps["complete-all-path-events"].params.get("input_dir"),
            "out/all_paths",
        )

    def test_sensitive_fields_are_rejected(self) -> None:
        config_path = self.write_config(
            {
                "run_id": "demo_sensitive",
                "input_text": "王佛脱险记.txt",
                "api_key": "secret",
                "steps": {"extract-events": {"enabled": True}},
            }
        )

        with self.assertRaises(PipelineConfigError):
            load_pipeline_config(config_path)


if __name__ == "__main__":
    unittest.main()
