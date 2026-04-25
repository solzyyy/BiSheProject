from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.pipeline.config import PipelineConfig
from src.pipeline.run_state import (
    create_run_state,
    mark_run_finished,
    mark_step_failed,
    mark_step_skipped,
    mark_step_started,
    mark_step_succeeded,
    record_event,
    save_run_state,
)


@dataclass
class StepResult:
    output_paths: list[str] = field(default_factory=list)
    validation_status: str | None = None


StepHandler = Callable[[PipelineConfig, str, dict[str, Any]], StepResult | None]


class PipelineExecutionError(RuntimeError):
    """Raised when a pipeline step fails during execution."""


def _normalize_step_result(result: StepResult | None) -> StepResult:
    if result is None:
        return StepResult()
    return result


def run_pipeline(
    config: PipelineConfig,
    step_handlers: dict[str, StepHandler],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if config.resume_from is not None and config.resume_from not in config.steps:
        raise PipelineExecutionError(f"Unknown resume_from step: {config.resume_from}")

    state = create_run_state(config, metadata=metadata)
    output_root = Path(config.output_root)
    events_path = output_root / "events.jsonl"

    save_run_state(state)
    record_event(events_path, {"type": "run_started", "run_id": config.run_id})

    started = config.resume_from is None

    for step_name, step_config in config.steps.items():
        if not started:
            if step_name == config.resume_from:
                started = True
            else:
                mark_step_skipped(state, step_name, reason="skipped_before_resume_point")
                continue

        if not step_config.enabled:
            mark_step_skipped(state, step_name, reason="disabled")
            continue

        handler = step_handlers.get(step_name)
        if handler is None:
            raise PipelineExecutionError(f"Missing handler for pipeline step: {step_name}")

        mark_step_started(state, step_name)
        save_run_state(state)
        record_event(events_path, {"type": "step_started", "step": step_name})

        try:
            result = _normalize_step_result(handler(config, step_name, state))
        except Exception as exc:
            mark_step_failed(state, step_name, str(exc))
            save_run_state(state)
            record_event(
                events_path,
                {"type": "step_failed", "step": step_name, "error": str(exc)},
            )
            raise PipelineExecutionError(f"Pipeline step failed: {step_name}") from exc

        mark_step_succeeded(
            state,
            step_name,
            output_paths=result.output_paths,
            validation_status=result.validation_status,
        )
        save_run_state(state)
        record_event(
            events_path,
            {
                "type": "step_succeeded",
                "step": step_name,
                "output_paths": result.output_paths,
                "validation_status": result.validation_status,
            },
        )

    mark_run_finished(state, status="passed")
    save_run_state(state)
    record_event(events_path, {"type": "run_finished", "run_id": config.run_id, "status": "passed"})
    return state
