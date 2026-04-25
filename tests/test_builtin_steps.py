import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from src.pipeline.builtin_steps import (
    BUILTIN_STEP_SPECS,
    params_to_cli_args,
    _resolve_output_paths,
    build_builtin_step_handlers,
    make_builtin_step_handler,
)
from src.pipeline.config import PipelineConfig, StepConfig


class ParamsToCliArgsTests(unittest.TestCase):
    def test_string_params(self) -> None:
        result = params_to_cli_args({"output": "out/foo.json", "input": "bar.txt"})
        self.assertEqual(result, ["--output", "out/foo.json", "--input", "bar.txt"])

    def test_bool_params(self) -> None:
        result = params_to_cli_args({"clear": True, "resume": False})
        self.assertEqual(result, ["--clear", "--no-resume"])

    def test_none_values_are_skipped(self) -> None:
        result = params_to_cli_args({"output": None, "input": "a.txt"})
        self.assertEqual(result, ["--input", "a.txt"])

    def test_int_params(self) -> None:
        result = params_to_cli_args({"max_concurrent": 5})
        self.assertEqual(result, ["--max-concurrent", "5"])

    def test_list_params(self) -> None:
        result = params_to_cli_args({"tags": ["a", "b"]})
        self.assertEqual(result, ["--tags", "a", "--tags", "b"])

    def test_underscore_to_hyphen(self) -> None:
        result = params_to_cli_args({"output_dir": "out"})
        self.assertEqual(result, ["--output-dir", "out"])

    def test_generate_branch_typer_flag_overrides(self) -> None:
        result = params_to_cli_args(
            {"max_branches_per_point": 3, "fork_event_id": "E9"},
            command_name="generate-branch",
        )
        self.assertIn("--max-branches", result)
        self.assertIn("3", result)
        self.assertIn("--fork-event", result)
        self.assertIn("E9", result)
        joined = " ".join(result)
        self.assertNotIn("--max-branches-per-point", joined)
        self.assertNotIn("--fork-event-id", joined)


class ResolveOutputPathsTests(unittest.TestCase):
    def test_uses_output_param_over_default(self) -> None:
        spec = BUILTIN_STEP_SPECS["extract-events"]
        paths = _resolve_output_paths(spec, {"output": "custom/path.json"})
        self.assertEqual(paths, ["custom/path.json"])

    def test_uses_output_dir_param(self) -> None:
        spec = BUILTIN_STEP_SPECS["generate-content"]
        paths = _resolve_output_paths(spec, {"output_dir": "custom/dir"})
        self.assertEqual(paths, ["custom/dir"])

    def test_falls_back_to_spec_defaults(self) -> None:
        spec = BUILTIN_STEP_SPECS["extract-events"]
        paths = _resolve_output_paths(spec, {})
        self.assertEqual(paths, ["out/extract_chain.json"])


class MakeHandlerTests(unittest.TestCase):
    @patch("src.pipeline.builtin_steps.subprocess.run")
    def test_handler_calls_subprocess_with_correct_command(self, mock_run: MagicMock) -> None:
        spec = BUILTIN_STEP_SPECS["extract-events"]
        handler = make_builtin_step_handler(spec, Path("/fake/root"))

        config = PipelineConfig(
            run_id="test",
            input_text="a.txt",
            output_root="runs/test",
            validate_after_each_step=False,
            resume_from=None,
            default_step_params={},
            steps={"extract-events": StepConfig(enabled=True, params={"input": "custom.txt"})},
        )

        result = handler(config, "extract-events", {})

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        self.assertIn("src.cli", cmd)
        self.assertIn("extract-events", cmd)
        self.assertIn("--input", cmd)
        self.assertIn("custom.txt", cmd)
        self.assertEqual(call_args[1]["cwd"], str(Path("/fake/root")))
        self.assertEqual(result.output_paths, ["out/extract_chain.json"])


class BuildHandlersTests(unittest.TestCase):
    def test_build_returns_all_builtin_steps(self) -> None:
        handlers = build_builtin_step_handlers(Path("/fake"))
        for step_name in BUILTIN_STEP_SPECS:
            self.assertIn(step_name, handlers, f"Missing handler for {step_name}")
        self.assertTrue(callable(handlers["extract-events"]))


if __name__ == "__main__":
    unittest.main()
