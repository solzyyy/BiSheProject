import json
import tempfile
import unittest
from pathlib import Path

from src.pipeline.config import PipelineConfig, StepConfig
from src.pipeline.run_state import (
    create_run_state,
    load_run_state,
    record_event,
    save_run_state,
    mark_step_failed,
    mark_step_started,
    mark_step_succeeded,
)


class RunStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp())
        self.output_root = self.tmpdir / "runs" / "demo_wangfo"
        self.config = PipelineConfig(
            run_id="demo_wangfo",
            input_text="王佛脱险记.txt",
            output_root=str(self.output_root),
            validate_after_each_step=True,
            resume_from=None,
            default_step_params={},
            steps={
                "extract-events": StepConfig(enabled=True, params={}),
                "generate-content": StepConfig(enabled=False, params={}),
            },
        )

    def test_create_run_state_writes_initial_file(self) -> None:
        state = create_run_state(
            self.config,
            metadata={
                "model_name": "gemini-2.5-flash",
                "prompt_version": "v1",
            },
        )

        save_run_state(state)
        loaded = load_run_state(self.output_root / "state.json")

        self.assertEqual(loaded["run_id"], "demo_wangfo")
        self.assertEqual(loaded["status"], "pending")
        self.assertEqual(loaded["steps"]["extract-events"]["status"], "pending")
        self.assertEqual(loaded["metadata"]["model_name"], "gemini-2.5-flash")

    def test_mark_step_started_updates_current_step(self) -> None:
        state = create_run_state(self.config)

        mark_step_started(state, "extract-events")

        self.assertEqual(state["status"], "running")
        self.assertEqual(state["current_step"], "extract-events")
        self.assertEqual(state["steps"]["extract-events"]["status"], "running")
        self.assertIsNotNone(state["steps"]["extract-events"]["started_at"])

    def test_mark_step_succeeded_records_outputs_and_validation(self) -> None:
        state = create_run_state(self.config)
        mark_step_started(state, "extract-events")

        mark_step_succeeded(
            state,
            "extract-events",
            output_paths=["out/extract_chain.json"],
            validation_status="passed",
        )

        step = state["steps"]["extract-events"]
        self.assertEqual(step["status"], "passed")
        self.assertEqual(step["output_paths"], ["out/extract_chain.json"])
        self.assertEqual(step["validation_status"], "passed")
        self.assertIsNotNone(step["finished_at"])

    def test_mark_step_failed_records_error(self) -> None:
        state = create_run_state(self.config)
        mark_step_started(state, "extract-events")

        mark_step_failed(state, "extract-events", "network error")

        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["steps"]["extract-events"]["status"], "failed")
        self.assertEqual(state["steps"]["extract-events"]["error"], "network error")

    def test_record_event_appends_jsonl_log(self) -> None:
        events_path = self.output_root / "events.jsonl"
        record_event(events_path, {"type": "run_started", "run_id": "demo_wangfo"})
        record_event(events_path, {"type": "step_started", "step": "extract-events"})

        lines = events_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["type"], "run_started")
        self.assertEqual(json.loads(lines[1])["step"], "extract-events")


if __name__ == "__main__":
    unittest.main()
