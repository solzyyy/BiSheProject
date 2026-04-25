import json
import tempfile
import unittest
from pathlib import Path

from src.pipeline.config import PipelineConfig, StepConfig
from src.pipeline.runner import PipelineExecutionError, StepResult, run_pipeline


class PipelineRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp())
        self.output_root = self.tmpdir / "runs" / "demo_wangfo"

    def build_config(self, *, resume_from: str | None = None, disable_last_step: bool = False) -> PipelineConfig:
        return PipelineConfig(
            run_id="demo_wangfo",
            input_text="王佛脱险记.txt",
            output_root=str(self.output_root),
            validate_after_each_step=True,
            resume_from=resume_from,
            default_step_params={},
            steps={
                "extract-events": StepConfig(enabled=True, params={}),
                "generate-all-paths": StepConfig(enabled=True, params={}),
                "generate-content": StepConfig(enabled=not disable_last_step, params={}),
            },
        )

    def test_run_pipeline_executes_enabled_steps_and_writes_state_files(self) -> None:
        config = self.build_config(disable_last_step=True)
        calls: list[str] = []

        def extract_handler(_config: PipelineConfig, step_name: str, _state: dict) -> StepResult:
            calls.append(step_name)
            return StepResult(output_paths=["out/extract_chain.json"], validation_status="passed")

        def generate_paths_handler(_config: PipelineConfig, step_name: str, _state: dict) -> StepResult:
            calls.append(step_name)
            return StepResult(output_paths=["out/all_paths/index.json"], validation_status="passed")

        state = run_pipeline(
            config,
            step_handlers={
                "extract-events": extract_handler,
                "generate-all-paths": generate_paths_handler,
            },
            metadata={"trigger": "unit-test"},
        )

        self.assertEqual(calls, ["extract-events", "generate-all-paths"])
        self.assertEqual(state["status"], "passed")
        self.assertIsNone(state["current_step"])
        self.assertEqual(state["steps"]["extract-events"]["status"], "passed")
        self.assertEqual(state["steps"]["generate-all-paths"]["status"], "passed")
        self.assertEqual(state["steps"]["generate-content"]["status"], "skipped")

        state_path = self.output_root / "state.json"
        events_path = self.output_root / "events.jsonl"
        self.assertTrue(state_path.exists())
        self.assertTrue(events_path.exists())

        saved_state = json.loads(state_path.read_text(encoding="utf-8"))
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(saved_state["metadata"]["trigger"], "unit-test")
        self.assertEqual(
            [event["type"] for event in events],
            ["run_started", "step_started", "step_succeeded", "step_started", "step_succeeded", "run_finished"],
        )

    def test_run_pipeline_honors_resume_from(self) -> None:
        config = self.build_config(resume_from="generate-all-paths")
        calls: list[str] = []

        def handler(_config: PipelineConfig, step_name: str, _state: dict) -> StepResult:
            calls.append(step_name)
            return StepResult()

        state = run_pipeline(
            config,
            step_handlers={
                "generate-all-paths": handler,
                "generate-content": handler,
            },
        )

        self.assertEqual(calls, ["generate-all-paths", "generate-content"])
        self.assertEqual(state["steps"]["extract-events"]["status"], "skipped")
        self.assertEqual(state["steps"]["extract-events"]["error"], "skipped_before_resume_point")
        self.assertEqual(state["steps"]["generate-all-paths"]["status"], "passed")

    def test_run_pipeline_marks_failure_and_stops_following_steps(self) -> None:
        config = self.build_config()
        calls: list[str] = []

        def extract_handler(_config: PipelineConfig, step_name: str, _state: dict) -> StepResult:
            calls.append(step_name)
            return StepResult(output_paths=["out/extract_chain.json"])

        def broken_handler(_config: PipelineConfig, step_name: str, _state: dict) -> StepResult:
            calls.append(step_name)
            raise RuntimeError("network timeout")

        with self.assertRaises(PipelineExecutionError):
            run_pipeline(
                config,
                step_handlers={
                    "extract-events": extract_handler,
                    "generate-all-paths": broken_handler,
                    "generate-content": extract_handler,
                },
            )

        self.assertEqual(calls, ["extract-events", "generate-all-paths"])

        saved_state = json.loads((self.output_root / "state.json").read_text(encoding="utf-8"))
        events = [
            json.loads(line)
            for line in (self.output_root / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]

        self.assertEqual(saved_state["status"], "failed")
        self.assertEqual(saved_state["steps"]["generate-all-paths"]["status"], "failed")
        self.assertEqual(saved_state["steps"]["generate-all-paths"]["error"], "network timeout")
        self.assertEqual(saved_state["steps"]["generate-content"]["status"], "pending")
        self.assertEqual(events[-1]["type"], "step_failed")


if __name__ == "__main__":
    unittest.main()
